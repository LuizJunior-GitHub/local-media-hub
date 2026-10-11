import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.engine.comfy_client import (
    ComfyUIClient,
    ComfyUIClientError,
    get_comfy_client,
)
from app.schemas.generation import (
    GenerationJobResponse,
    JobStatus,
    ModelEngine,
    utc_now,
)

logger = logging.getLogger(__name__)


class QueueItem:
    """Internal representation of a generation job in the queue."""

    def __init__(
        self,
        job_id: str,
        engine: ModelEngine,
        workflow: dict,
        seed: int,
        prompt: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.job_id = job_id
        self.engine = engine
        self.workflow = workflow
        self.seed = seed
        self.prompt = prompt
        self.metadata = metadata or {}


class QueueManager:
    """
    Asynchronous Queue Manager with Strict GPU Concurrency Control.
    Enforces a strict lock ensuring only 1 heavy inference job runs on the
    AMD Radeon RX 7800 XT (16 GB GDDR6) at a time, preventing OOM.
    """

    def __init__(self, comfy_client: Optional[ComfyUIClient] = None):
        self.settings = get_settings()
        self.comfy_client = comfy_client or get_comfy_client()
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._gpu_lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        # In-memory storage of job states and synchronization primitives
        self._jobs: Dict[str, GenerationJobResponse] = {}
        self._completion_events: Dict[str, asyncio.Event] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._active_job_id: Optional[str] = None

        # Statistics
        self._total_jobs_submitted = 0
        self._total_jobs_completed = 0
        self._total_jobs_failed = 0

    # --------------------------------------------------------------------------
    # Worker Lifecycle
    # --------------------------------------------------------------------------
    async def start(self) -> None:
        """Start the background queue consumer worker."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._worker_loop(), name="gpu-queue-worker")
            logger.info("GPU Queue Manager worker started successfully.")

    async def stop(self) -> None:
        """Gracefully stop the background worker."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("GPU Queue Manager worker stopped.")

    # --------------------------------------------------------------------------
    # Job Submission & State Query
    # --------------------------------------------------------------------------
    async def submit_job(
        self,
        engine: ModelEngine,
        workflow: dict,
        seed: int,
        prompt: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Enqueue a new generation job and return its unique job_id."""
        job_id = str(uuid.uuid4())
        now = utc_now()

        job_resp = GenerationJobResponse(
            job_id=job_id,
            status=JobStatus.QUEUED,
            engine=engine,
            prompt=prompt,
            created_at=now,
            metadata=metadata or {},
        )

        self._jobs[job_id] = job_resp
        self._completion_events[job_id] = asyncio.Event()
        self._subscribers[job_id] = []
        self._total_jobs_submitted += 1

        queue_item = QueueItem(
            job_id=job_id,
            engine=engine,
            workflow=workflow,
            seed=seed,
            prompt=prompt,
            metadata=metadata,
        )

        await self._queue.put(queue_item)
        logger.info(f"Job {job_id} [{engine.value}] enqueued. Current queue size: {self._queue.qsize()}")
        return job_id

    def get_job(self, job_id: str) -> Optional[GenerationJobResponse]:
        """Fetch the current state of a job."""
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> List[GenerationJobResponse]:
        """List recently submitted jobs."""
        all_jobs = list(self._jobs.values())
        return sorted(all_jobs, key=lambda j: j.created_at, reverse=True)[:limit]

    async def wait_for_job(self, job_id: str, timeout: Optional[float] = None) -> GenerationJobResponse:
        """
        Wait until job finishes (COMPLETED, FAILED, or CANCELLED) or times out.
        """
        event = self._completion_events.get(job_id)
        if not event:
            raise KeyError(f"Job {job_id} not found")

        effective_timeout = timeout or float(self.settings.comfyui_timeout_seconds)
        try:
            await asyncio.wait_for(event.wait(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            job = self._jobs.get(job_id)
            if job and job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                job.status = JobStatus.FAILED
                job.error = f"Job timed out after {effective_timeout}s"
                self._notify_subscribers(job)
            raise TimeoutError(f"Job {job_id} exceeded timeout of {effective_timeout}s")

        return self._jobs[job_id]

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel an enqueued or currently executing job."""
        job = self._jobs.get(job_id)
        if not job or job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return False

        if self._active_job_id == job_id:
            # Active on GPU: interrupt ComfyUI
            await self.comfy_client.interrupt()

        job.status = JobStatus.CANCELLED
        job.completed_at = utc_now()
        job.error = "Cancelled by user"

        if job_id in self._completion_events:
            self._completion_events[job_id].set()

        self._notify_subscribers(job)
        return True

    # --------------------------------------------------------------------------
    # Progress Pub/Sub (for WebSockets & SSE)
    # --------------------------------------------------------------------------
    def subscribe(self, job_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a job."""
        sub_queue = asyncio.Queue()
        if job_id in self._subscribers:
            self._subscribers[job_id].append(sub_queue)
        else:
            self._subscribers[job_id] = [sub_queue]
        return sub_queue

    def unsubscribe(self, job_id: str, sub_queue: asyncio.Queue) -> None:
        """Unsubscribe from progress updates."""
        if job_id in self._subscribers and sub_queue in self._subscribers[job_id]:
            self._subscribers[job_id].remove(sub_queue)

    def _notify_subscribers(self, job: GenerationJobResponse) -> None:
        """Send a snapshot copy of job state to all active subscribers."""
        subs = self._subscribers.get(job.job_id, [])
        for sub in list(subs):
            try:
                sub.put_nowait(job.model_copy(deep=True))
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # Worker Execution Loop
    # --------------------------------------------------------------------------
    async def _worker_loop(self) -> None:
        """
        Pulls jobs one at a time and runs them under GPU lock.
        Strictly guarantees at most 1 heavy job on the GPU at any moment.
        """
        while self._running:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break

            job = self._jobs.get(item.job_id)
            if not job or job.status == JobStatus.CANCELLED:
                self._queue.task_done()
                continue

            async with self._gpu_lock:
                self._active_job_id = item.job_id
                start_time = time.perf_counter()

                job.status = JobStatus.PROCESSING
                job.started_at = utc_now()
                job.progress = 0.0
                self._notify_subscribers(job)

                def on_progress(step: int, max_steps: int) -> None:
                    job.current_step = step
                    job.total_steps = max_steps
                    job.progress = float(step) / float(max(max_steps, 1))
                    self._notify_subscribers(job)

                try:
                    logger.info(f"Starting GPU execution for job {item.job_id} [{item.engine.value}]")
                    images = await self.comfy_client.execute_and_wait(
                        workflow_prompt=item.workflow,
                        seed=item.seed,
                        on_progress=on_progress,
                    )

                    duration = time.perf_counter() - start_time
                    job.status = JobStatus.COMPLETED
                    job.completed_at = utc_now()
                    job.progress = 1.0
                    job.images = images
                    job.execution_time_seconds = round(duration, 2)
                    self._total_jobs_completed += 1
                    logger.info(f"Job {item.job_id} finished in {duration:.2f}s with {len(images)} images.")

                except Exception as exc:
                    duration = time.perf_counter() - start_time
                    job.status = JobStatus.FAILED
                    job.completed_at = utc_now()
                    job.error = str(exc)
                    job.execution_time_seconds = round(duration, 2)
                    self._total_jobs_failed += 1
                    logger.error(f"Job {item.job_id} failed after {duration:.2f}s: {exc}", exc_info=True)

                finally:
                    self._active_job_id = None
                    if item.job_id in self._completion_events:
                        self._completion_events[item.job_id].set()
                    self._notify_subscribers(job)
                    self._queue.task_done()

    # --------------------------------------------------------------------------
    # Queue Metrics
    # --------------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        """Return operational metrics of the queue and GPU lock."""
        return {
            "queue_length": self._queue.qsize(),
            "active_job_id": self._active_job_id,
            "gpu_locked": self._gpu_lock.locked(),
            "max_concurrent_gpu_jobs": self.settings.max_concurrent_gpu_jobs,
            "total_submitted": self._total_jobs_submitted,
            "total_completed": self._total_jobs_completed,
            "total_failed": self._total_jobs_failed,
        }


_queue_manager_instance: Optional[QueueManager] = None


def get_queue_manager() -> QueueManager:
    global _queue_manager_instance
    if _queue_manager_instance is None:
        _queue_manager_instance = QueueManager()
    return _queue_manager_instance
