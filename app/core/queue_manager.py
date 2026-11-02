import asyncio
import logging
import time
import uuid
import json
import hashlib
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

class JobConflict(ValueError):
    pass

class QueueBlocked(RuntimeError):
    pass


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
        self._job_dir = self.settings.storage_base_dir / "jobs"
        self._job_dir.mkdir(parents=True, exist_ok=True)
        self._request_index: Dict[str, str] = {}
        for path in self._job_dir.glob("*.json"):
            try:
                job = GenerationJobResponse.model_validate_json(path.read_text(encoding="utf-8"))
                if job.status in (JobStatus.QUEUED, JobStatus.PENDING):
                    # PROCESSING is persisted before sending. A queued record
                    # cannot have reached ComfyUI; it is safe to allow manual retry.
                    job.status = JobStatus.FAILED
                    job.error = "Server restarted before this queued job was submitted; retry explicitly"
                elif job.status == JobStatus.PROCESSING:
                    job.status = JobStatus.UNCERTAIN
                    job.error = "Server restarted; reconcile the original execution before retrying"
                self._jobs[job.job_id] = job
                event = asyncio.Event()
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    event.set()
                self._completion_events[job.job_id] = event
                self._subscribers[job.job_id] = []
                if job.metadata.get("request_id"):
                    self._request_index[job.metadata["request_id"]] = job.job_id
            except Exception:
                logger.exception("Cannot restore job journal %s", path)
                raise RuntimeError(f"Invalid job journal: {path}; refusing unsafe restart")

    def _persist(self, job):
        path = self._job_dir / f"{job.job_id}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(path)

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
        request_id: Optional[str] = None,
    ) -> str:
        """Enqueue a new generation job and return its unique job_id."""
        fingerprint = hashlib.sha256(json.dumps({"engine":engine.value,"workflow":workflow,"seed":seed,
            "prompt":prompt,"metadata":metadata or {}}, sort_keys=True).encode()).hexdigest()
        if request_id and request_id in self._request_index:
            existing = self._jobs[self._request_index[request_id]]
            if existing.metadata.get("fingerprint") != fingerprint:
                raise JobConflict("request_id already used with different generation parameters")
            return existing.job_id
        if any(j.status == JobStatus.UNCERTAIN for j in self._jobs.values()):
            raise QueueBlocked("Uncertain job exists; reconcile original execution before submitting more GPU work")
        job_id = str(uuid.uuid4())
        now = utc_now()

        job_resp = GenerationJobResponse(
            job_id=job_id,
            status=JobStatus.QUEUED,
            engine=engine,
            prompt=prompt,
            created_at=now,
            metadata={**(metadata or {}), "seed":seed, "request_id":request_id, "fingerprint":fingerprint,
                      "workflow":workflow},
        )

        self._jobs[job_id] = job_resp
        self._persist(job_resp)
        if request_id:
            self._request_index[request_id] = job_id
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
            raise TimeoutError(f"Job {job_id} exceeded timeout of {effective_timeout}s")

        return self._jobs[job_id]

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel an enqueued or currently executing job."""
        job = self._jobs.get(job_id)
        if not job or job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return False
        if job.status == JobStatus.UNCERTAIN:
            # A restored execution may still exist in ComfyUI. Cancellation
            # cannot be claimed without reconciling original ownership first.
            return False

        if self._active_job_id == job_id:
            # Active on GPU: interrupt ComfyUI
            if not await self.comfy_client.interrupt():
                return False
            job.metadata["cancel_requested"] = True
            self._notify_subscribers(job)
            return True

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
        self._persist(job)
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
            while self._running and any(j.status == JobStatus.UNCERTAIN for j in self._jobs.values()):
                await asyncio.sleep(1)
            if job.status == JobStatus.CANCELLED:
                self._queue.task_done()
                continue

            async with self._gpu_lock:
                if job.status == JobStatus.CANCELLED:
                    self._queue.task_done()
                    continue
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
                    def submitted(prompt_id):
                        job.prompt_id = prompt_id
                        self._persist(job)
                    kwargs = {"on_submitted":submitted,
                              "is_cancelled":lambda:bool(job.metadata.get("cancel_requested"))} if isinstance(self.comfy_client, ComfyUIClient) else {}
                    images = await self.comfy_client.execute_and_wait(
                        workflow_prompt=item.workflow,
                        seed=item.seed,
                        on_progress=on_progress,
                        **kwargs,
                    )

                    duration = time.perf_counter() - start_time
                    job.status = JobStatus.CANCELLED if job.metadata.get("cancel_requested") else JobStatus.COMPLETED
                    job.completed_at = utc_now()
                    job.progress = 1.0
                    job.images = images
                    job.execution_time_seconds = round(duration, 2)
                    if job.status == JobStatus.COMPLETED:
                        self._total_jobs_completed += 1
                    logger.info(f"Job {item.job_id} finished in {duration:.2f}s with {len(images)} images.")

                except Exception as exc:
                    duration = time.perf_counter() - start_time
                    job.status = (JobStatus.CANCELLED if job.metadata.get("cancel_requested") else
                                  JobStatus.UNCERTAIN if "Uncertain execution" in str(exc) else JobStatus.FAILED)
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
            "uncertain_jobs": [j.job_id for j in self._jobs.values() if j.status == JobStatus.UNCERTAIN],
        }

    async def reconcile_job(self, job_id: str):
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.status != JobStatus.UNCERTAIN:
            return job
        if isinstance(self.comfy_client,ComfyUIClient) and job.metadata.get('workflow'):
            images=self.comfy_client.recover_local_output(job.metadata['workflow'],job.metadata['seed'],job.prompt_id or job.job_id)
            if images:
                job.images=images;job.status=JobStatus.COMPLETED;job.progress=1.0;job.error=None;job.completed_at=utc_now()
                job.metadata['recovery_method']='Exact workflow match in original ComfyUI PNG'
                self._completion_events[job_id].set();self._notify_subscribers(job);return job
        if not job.prompt_id and job.metadata.get("workflow"):
            # Lost acknowledgement: match the exact submitted graph and seed,
            # never presume that a PROCESSING record was not submitted.
            session = await self.comfy_client.get_session()
            async with session.get(f"{self.settings.comfy_http_url}/history") as response:
                response.raise_for_status()
                records = await response.json()
            matches = [pid for pid,record in records.items()
                       if len(record.get("prompt",[]))>2 and record["prompt"][2]==job.metadata["workflow"]]
            if len(matches)==1:
                job.prompt_id = matches[0]
                self._persist(job)
        if not job.prompt_id:
            return job
        history = await self.comfy_client.get_history(job.prompt_id)
        if history:
            if history.get("status", {}).get("status_str") == "error":
                job.status = JobStatus.FAILED
                job.error = str(history.get("status", {}).get("messages"))
            elif history.get("status", {}).get("completed") or history.get("outputs"):
                job.images = await self.comfy_client.collect_outputs(job.prompt_id, history, job.metadata["seed"])
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.error = None
            if job.status != JobStatus.UNCERTAIN:
                job.completed_at = utc_now()
                self._completion_events[job_id].set()
                self._notify_subscribers(job)
        return job


_queue_manager_instance: Optional[QueueManager] = None


def get_queue_manager() -> QueueManager:
    global _queue_manager_instance
    if _queue_manager_instance is None:
        _queue_manager_instance = QueueManager()
    return _queue_manager_instance
