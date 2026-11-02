import asyncio
import pytest
from app.services import quiz_production as p
from app.api import routes_quiz as q
from app.core.queue_manager import QueueManager
from app.schemas.generation import JobStatus, ModelEngine
from app.engine.template_engine import get_template_engine
from tests.test_queue_manager import MockComfyUIClient
from tests.test_reliability import manifest

@pytest.fixture
def queue(monkeypatch):
    queue = QueueManager(MockComfyUIClient())
    monkeypatch.setattr(p, 'get_queue_manager', lambda: queue)
    monkeypatch.setattr(q, 'get_queue_manager', lambda: queue)
    return queue

async def project():
    await q.import_manifest(q.ImportRequest(manifest=manifest(), project_id='hybrid'))

@pytest.mark.asyncio
async def test_manual_review_and_selection_shared(queue):
    await project()
    await p.configure('hybrid', {'manual_review': True, 'global_selection': {'P_bg': False}})
    state = await p.get_project('hybrid')
    assert state['slots']['001P']['status'] == 'blocked'
    assert not state['slots']['001P_bg']['enabled']
    with pytest.raises(ValueError, match='Revise'):
        await p.generate_slot('hybrid', '001P', 42, engine='flux')
    await p.edit_item('hybrid', 'slot', '001P', {'reviewed': True})
    job = await p.generate_slot('hybrid', '001P', 42, engine='flux')
    assert job.status == JobStatus.QUEUED
    await p.edit_item('hybrid', 'slot', '001P_bg', {'selection_override': True})
    assert (await p.get_project('hybrid'))['slots']['001P_bg']['enabled']

@pytest.mark.asyncio
async def test_pause_blocks_new_submission_and_can_resume_before_job_finishes(queue):
    await project()
    started = await p.start_batch('hybrid', 'scenes', ['001P'])
    owner = started['batch']['operation_id']
    await p.pause_batch('hybrid')
    with pytest.raises(ValueError, match='pausada'):
        await p.generate_slot('hybrid', '001P', 42, engine='flux', batch_owner=owner)
    restarted = await p.start_batch('hybrid', 'scenes', ['001P'])
    assert restarted['batch']['status'] == 'running'
    assert restarted['batch']['operation_id'] != owner
    await p.stop_batches()
    p.BATCH_TASKS.clear()

@pytest.mark.asyncio
async def test_completed_image_preserved_when_prompt_changes(queue):
    await project()
    job = await p.generate_slot('hybrid', '001P', 42, engine='flux')
    job.status = JobStatus.COMPLETED
    await p.edit_item('hybrid', 'slot', '001P', {'prompt': 'A new framing'})
    state = await p.get_project('hybrid')
    assert state['slots']['001P']['stale']
    assert state['slots']['001P']['job_id'] == job.job_id
    assert (await p.generate_slot('hybrid', '001P', 43, engine='flux', attempt_id='new')).job_id == job.job_id

def test_klein_uses_each_image_in_both_conditioning_chains():
    graph, _ = get_template_engine().build_klein_workflow('scene', 42, reference_images=['one.png', 'two.png', 'three.png'])
    assert sum(n['class_type'] == 'LoadImage' for n in graph.values()) == 3
    assert sum(n['class_type'] == 'ReferenceLatent' for n in graph.values()) == 6
    guider = next(n for n in graph.values() if n['class_type'] == 'CFGGuider')
    assert graph[guider['inputs']['positive'][0]]['class_type'] == 'ReferenceLatent'
    assert graph[guider['inputs']['negative'][0]]['class_type'] == 'ReferenceLatent'

@pytest.mark.asyncio
async def test_restore_choices_keeps_original_job_identity(queue):
    import copy
    await project()
    job=await p.generate_slot('hybrid','001P',42,engine='flux')
    original=copy.deepcopy(await p.get_project('hybrid'))
    original['slots']['001P']['job_id']='forged-job'
    await p.edit_item('hybrid','slot','001P',{'prompt':'Changed prompt'})
    restored=await p.restore_snapshot('hybrid',original)
    assert restored['slots']['001P']['job_id']==job.job_id
    assert restored['slots']['001P']['prompt']=='Ancient ark'
    invalid=copy.deepcopy(original)
    invalid['slots']['001R']['reference_ids']=['unknown']
    with pytest.raises(ValueError):await p.restore_snapshot('hybrid',invalid)

@pytest.mark.asyncio
async def test_simultaneous_editor_detects_mcp_change_without_losing_either_text(queue):
    await project()
    await p.edit_item('hybrid','slot','001P',{'prompt':'MCP revision'})
    with pytest.raises(ValueError,match='Outra operação'):
        await p.edit_item('hybrid','slot','001P',{'prompt':'Human revision','expected':{'prompt':'Ancient ark'}})
    assert (await p.get_project('hybrid'))['slots']['001P']['prompt']=='MCP revision'

@pytest.mark.asyncio
async def test_experimental_steps_setting_validated(queue):
    await project()
    assert (await p.configure('hybrid',{'klein_steps':8}))['settings']['klein_steps']==8
    for invalid in [0,17,True,4.5,'8']:
        with pytest.raises(ValueError):await p.configure('hybrid',{'klein_steps':invalid})

def test_klein_steps_change_only_scheduler():
    import copy
    a,_=get_template_engine().build_klein_workflow('scene',42,reference_images=['one.png'],steps=4)
    for steps in [8,12]:
        b,_=get_template_engine().build_klein_workflow('scene',42,reference_images=['one.png'],steps=steps)
        expected=copy.deepcopy(a);expected['7']['inputs']['steps']=steps
        assert b==expected
