import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.core.queue_manager import QueueManager
from app.schemas.generation import GeneratedImageInfo, JobStatus, ModelEngine


class MockComfyUIClient:
    """Mock ComfyUI client for testing queue and execution synchronization."""

    def __init__(self, execution_delay: float = 0.05):
        self.execution_delay = execution_delay
        self.active_executions = 0
        self.max_concurrent_seen = 0

    async def execute_and_wait(self, workflow_prompt: dict, seed: int, on_progress=None):
        self.active_executions += 1
        self.max_concurrent_seen = max(self.max_concurrent_seen, self.active_executions)

        if on_progress:
            on_progress(10, 30)
            await asyncio.sleep(self.execution_delay / 2)
            on_progress(30, 30)

        await asyncio.sleep(self.execution_delay / 2)
        self.active_executions -= 1

        return [
            GeneratedImageInfo(
                filename="mock_output.png",
                subfolder="",
                url="/outputs/mock_output.png",
                file_path="/mock/path/mock_output.png",
                content_type="image/png",
                size_bytes=1024,
                width=1024,
                height=1024,
                seed=seed,
            )
        ]

    async def interrupt(self):
        return True


@pytest.mark.asyncio
async def test_queue_single_gpu_concurrency_lock():
    """
    Verify the strict GPU lock invariant: Even if multiple jobs are submitted
    simultaneously, at most 1 heavy job executes on the GPU at any single moment.
    """
    mock_client = MockComfyUIClient(execution_delay=0.08)
    queue_mgr = QueueManager(comfy_client=mock_client)
    await queue_mgr.start()

    try:
        # Submit 3 jobs in rapid succession
        job1_id = await queue_mgr.submit_job(ModelEngine.SDXL, {}, 101, "Job 1")
        job2_id = await queue_mgr.submit_job(ModelEngine.FLUX_SCHNELL, {}, 102, "Job 2")
        job3_id = await queue_mgr.submit_job(ModelEngine.SDXL, {}, 103, "Job 3")

        # Wait for all to complete concurrently
        res1, res2, res3 = await asyncio.gather(
            queue_mgr.wait_for_job(job1_id),
            queue_mgr.wait_for_job(job2_id),
            queue_mgr.wait_for_job(job3_id),
        )

        assert res1.status == JobStatus.COMPLETED
        assert res2.status == JobStatus.COMPLETED
        assert res3.status == JobStatus.COMPLETED

        # CRITICAL TEST: Maximum concurrent execution on GPU must be strictly 1
        assert mock_client.max_concurrent_seen == 1

        stats = queue_mgr.get_stats()
        assert stats["total_completed"] == 3
        assert stats["queue_length"] == 0

    finally:
        await queue_mgr.stop()


@pytest.mark.asyncio
async def test_queue_job_cancellation():
    mock_client = MockComfyUIClient(execution_delay=0.1)
    queue_mgr = QueueManager(comfy_client=mock_client)
    await queue_mgr.start()

    try:
        job_id = await queue_mgr.submit_job(ModelEngine.SDXL, {}, 999, "To be cancelled")
        cancelled = await queue_mgr.cancel_job(job_id)
        assert cancelled is True

        job = queue_mgr.get_job(job_id)
        assert job.status == JobStatus.CANCELLED
    finally:
        await queue_mgr.stop()
