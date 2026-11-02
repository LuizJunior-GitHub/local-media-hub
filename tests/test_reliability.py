import asyncio
import pytest
import json
from app.core.queue_manager import QueueManager
from app.core.config import get_settings
from app.schemas.generation import JobStatus, ModelEngine
from app.api.routes_quiz import normalize
import app.api.routes_quiz as quiz
from tests.test_queue_manager import MockComfyUIClient

@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(),"storage_base_dir",tmp_path)

@pytest.mark.asyncio
async def test_idempotency_survives_restart(isolated_store):
    queue = QueueManager(MockComfyUIClient())
    await queue.start()
    try:
        first = await queue.submit_job(ModelEngine.SDXL,{},1,"scene",request_id="one")
        second = await queue.submit_job(ModelEngine.SDXL,{},1,"scene",request_id="one")
        assert first == second
        await queue.wait_for_job(first)
        restored = QueueManager(MockComfyUIClient())
        assert restored.get_job(first).status == JobStatus.COMPLETED
        assert await restored.submit_job(ModelEngine.SDXL,{},1,"scene",request_id="one") == first
        with pytest.raises(ValueError):
            await restored.submit_job(ModelEngine.SDXL,{},2,"changed",request_id="one")
    finally:
        await queue.stop()

@pytest.mark.asyncio
async def test_wait_timeout_does_not_fail_or_resubmit(isolated_store):
    queue = QueueManager(MockComfyUIClient(execution_delay=.1))
    await queue.start()
    try:
        job_id = await queue.submit_job(ModelEngine.SDXL,{},1,"slow")
        with pytest.raises(TimeoutError):
            await queue.wait_for_job(job_id,timeout=.01)
        assert queue.get_job(job_id).status == JobStatus.PROCESSING
        assert (await queue.wait_for_job(job_id)).status == JobStatus.COMPLETED
    finally:
        await queue.stop()

@pytest.mark.asyncio
async def test_active_cancel_not_overwritten(isolated_store):
    queue = QueueManager(MockComfyUIClient(execution_delay=.1))
    await queue.start()
    try:
        job_id = await queue.submit_job(ModelEngine.SDXL,{},1,"cancel")
        await asyncio.sleep(.01)
        assert await queue.cancel_job(job_id)
        assert (await queue.wait_for_job(job_id)).status == JobStatus.CANCELLED
    finally:
        await queue.stop()

@pytest.mark.asyncio
async def test_restart_distinguishes_queued_from_submitted(isolated_store):
    queue = QueueManager(MockComfyUIClient())
    job_id = await queue.submit_job(ModelEngine.SDXL,{},1,"pending")
    restored = QueueManager(MockComfyUIClient())
    assert restored.get_job(job_id).status == JobStatus.FAILED
    job=queue.get_job(job_id)
    job.status=JobStatus.PROCESSING
    queue._persist(job)
    restored=QueueManager(MockComfyUIClient())
    assert restored.get_job(job_id).status == JobStatus.UNCERTAIN
    with pytest.raises(RuntimeError):
        await restored.submit_job(ModelEngine.SDXL,{},2,"new")

def manifest():
    return {"version":1,"project":{"title":"Ark"},"characters":[],"assets":[],
            "scenes":[{"sourceIndex":"001"+suffix,"prompt":"Ancient ark","tags":[]}
                      for suffix in ("P","R","P_bg","R_bg")]}

def test_manifest_validation():
    data = manifest()
    assert normalize(data)["slots"]["001P"]["filename"] == "001P.png"
    data["scenes"][0]["tags"] = ["unknown"]
    with pytest.raises(ValueError):
        normalize(data)

@pytest.mark.asyncio
async def test_concurrent_quiz_updates_and_explicit_retry(isolated_store,monkeypatch):
    queue=QueueManager(MockComfyUIClient())
    monkeypatch.setattr(quiz,"get_queue_manager",lambda:queue)
    import app.services.quiz_production as production
    monkeypatch.setattr(production,"get_queue_manager",lambda:queue)
    original=queue.submit_job
    async def delayed(*args,**kwargs):
        await asyncio.sleep(.01)
        return await original(*args,**kwargs)
    monkeypatch.setattr(queue,"submit_job",delayed)
    await quiz.import_manifest(quiz.ImportRequest(manifest=manifest(),project_id="parallel-test"))
    a,b=await asyncio.gather(*[quiz.generate_slot(quiz.SlotRequest(project_id="parallel-test",source_index=slot,seed=42))
                              for slot in ["001P","001R"]])
    data=json.loads(quiz.project_path("parallel-test").read_text(encoding="utf-8"))
    assert data["slots"]["001P"]["job_id"]==a.job_id
    assert data["slots"]["001R"]["job_id"]==b.job_id
    a.status=JobStatus.FAILED
    queue._persist(a)
    old=await quiz.generate_slot(quiz.SlotRequest(project_id="parallel-test",source_index="001P",seed=43))
    assert old.job_id==a.job_id
    retry=await quiz.generate_slot(quiz.SlotRequest(project_id="parallel-test",source_index="001P",seed=43,
                                                   retry_failed=True,attempt_id="retry1"))
    assert retry.job_id!=a.job_id
    same=await quiz.generate_slot(quiz.SlotRequest(project_id="parallel-test",source_index="001P",seed=43,
                                                  retry_failed=True,attempt_id="retry1"))
    assert same.job_id==retry.job_id

@pytest.mark.asyncio
async def test_export_reports_missing_png(isolated_store,monkeypatch):
    from app.schemas.generation import GeneratedImageInfo
    queue=QueueManager(MockComfyUIClient())
    monkeypatch.setattr(quiz,"get_queue_manager",lambda:queue)
    import app.services.quiz_production as production
    monkeypatch.setattr(production,"get_queue_manager",lambda:queue)
    await quiz.import_manifest(quiz.ImportRequest(manifest=manifest(),project_id="missing-png"))
    job=await quiz.generate_slot(quiz.SlotRequest(project_id="missing-png",source_index="001P",seed=42))
    job.status=JobStatus.COMPLETED
    job.images=[GeneratedImageInfo(filename="gone.png",url="/outputs/gone.png",file_path="/not-existing/gone.png",
                                   size_bytes=1,width=1024,height=576,seed=42)]
    result=await quiz.export_project("missing-png")
    assert "001P" in result["missing"] and not result["files"]
    assert result["errors"][0]["source_index"]=="001P"
    data = manifest()
    data["scenes"].pop()
    with pytest.raises(ValueError):
        normalize(data)
