import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import aiohttp
from PIL import Image

from app.core.config import get_settings
from app.schemas.generation import GeneratedImageInfo

logger = logging.getLogger(__name__)


class ComfyUIClientError(Exception):
    """Base exception for ComfyUI client errors."""
    pass


class ComfyUIConnectionError(ComfyUIClientError):
    """Raised when unable to connect to ComfyUI."""
    pass


class ComfyUIExecutionError(ComfyUIClientError):
    """Raised when workflow execution fails in ComfyUI."""
    pass


class ComfyUIClient:
    """
    Asynchronous HTTP and WebSocket client for headless ComfyUI.
    Handles workflow submission, real-time WebSocket step monitoring,
    image downloads, and reference image uploads.
    """

    def __init__(self):
        self.settings = get_settings()
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=25,connect=5,sock_read=15)
            self._session = aiohttp.ClientSession(timeout=timeout,connector=aiohttp.TCPConnector(limit=8,keepalive_timeout=30))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # --------------------------------------------------------------------------
    # Health and Stats
    # --------------------------------------------------------------------------
    async def is_healthy(self) -> bool:
        """Check if ComfyUI headless server is responding."""
        try:
            session = await self.get_session()
            async with session.get(f"{self.settings.comfy_http_url}/system_stats", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def get_system_stats(self) -> Dict[str, Any]:
        """Fetch system and GPU stats from ComfyUI."""
        try:
            session = await self.get_session()
            async with session.get(f"{self.settings.comfy_http_url}/system_stats") as resp:
                if resp.status == 200:
                    return await resp.json()
                raise ComfyUIConnectionError(f"ComfyUI returned HTTP {resp.status}")
        except aiohttp.ClientError as e:
            raise ComfyUIConnectionError(f"Could not connect to ComfyUI at {self.settings.comfy_http_url}: {e}")

    # --------------------------------------------------------------------------
    # Image Uploads (for IP-Adapter & ControlNet reference anchors)
    # --------------------------------------------------------------------------
    async def upload_image(self, file_path: Path, image_type: str = "input", overwrite: bool = True) -> str:
        """
        Upload an anchor image to ComfyUI's input directory so workflow nodes can load it.
        Returns the filename stored in ComfyUI.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Reference file {file_path} does not exist")

        if not await self.is_healthy():
            if self.settings.demo_fallback:
                logger.info(f"ComfyUI offline: Using local reference file {file_path.name}")
                return file_path.name

        session = await self.get_session()
        url = f"{self.settings.comfy_http_url}/upload/image"

        data = aiohttp.FormData()
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        data.add_field("image", file_bytes, filename=file_path.name, content_type="image/png")
        data.add_field("overwrite", "true" if overwrite else "false")
        data.add_field("type", image_type)

        try:
            async with session.post(url, data=data) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    return res.get("name", file_path.name)
                text = await resp.text()
                raise ComfyUIClientError(f"Failed to upload image to ComfyUI: HTTP {resp.status} - {text}")
        except aiohttp.ClientError as e:
            raise ComfyUIConnectionError(f"Connection failed during image upload: {e}")

    # --------------------------------------------------------------------------
    # Prompt Submission & Execution
    # --------------------------------------------------------------------------
    async def queue_prompt(self, workflow_prompt: dict) -> str:
        """
        Submit a workflow prompt graph to ComfyUI.
        Returns the assigned prompt_id.
        """
        session = await self.get_session()
        url = f"{self.settings.comfy_http_url}/prompt"
        payload = {
            "prompt": workflow_prompt,
            "client_id": self.settings.comfyui_client_id,
        }

        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prompt_id = data.get("prompt_id")
                    if not prompt_id:
                        raise ComfyUIClientError("ComfyUI response did not contain prompt_id")
                    return prompt_id
                text = await resp.text()
                raise ComfyUIClientError(f"ComfyUI prompt submission failed: HTTP {resp.status} - {text}")
        except aiohttp.ClientError as e:
            raise ComfyUIConnectionError(f"Connection failed during prompt submission: {e}")

    async def get_history(self, prompt_id: str) -> Optional[dict]:
        """Fetch execution history for a prompt ID."""
        session = await self.get_session()
        url = f"{self.settings.comfy_http_url}/history/{prompt_id}"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get(prompt_id)
                return None
        except Exception:
            return None

    async def download_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        """Download a generated image binary from ComfyUI /view endpoint."""
        session = await self.get_session()
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url = f"{self.settings.comfy_http_url}/view"

        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.read()
            text = await resp.text()
            raise ComfyUIClientError(f"Failed to download image {filename}: HTTP {resp.status} - {text}")

    # --------------------------------------------------------------------------
    # Execution Lifecycle via WebSocket
    # --------------------------------------------------------------------------
    async def execute_and_wait(self, workflow_prompt: dict, seed: int, on_progress=None,
                               on_submitted=None, resume_prompt_id=None, is_cancelled=None) -> List[GeneratedImageInfo]:
        session = await self.get_session()
        ws, progress_task = None, None
        observed_id = resume_prompt_id
        def submitted(prompt_id):
            nonlocal observed_id
            observed_id = prompt_id
            if on_submitted:
                on_submitted(prompt_id)
        async def monitor_progress():
            try:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(msg.data)
                    data = event.get("data",{})
                    if event.get("type")=="progress" and data.get("prompt_id")==observed_id and on_progress:
                        result=on_progress(data.get("value",0),data.get("max",1))
                        if asyncio.iscoroutine(result):
                            await result
            except (aiohttp.ClientError,ValueError):
                logger.warning("Progress stream disconnected; durable polling remains active")
        try:
            try:
                ws = await session.ws_connect(self.settings.comfy_ws_url,heartbeat=15,timeout=5)
                progress_task = asyncio.create_task(monitor_progress())
            except (aiohttp.ClientError,asyncio.TimeoutError):
                logger.warning("Progress stream unavailable; using durable polling")
            return await self._poll_execution(workflow_prompt,seed,on_progress,submitted,resume_prompt_id,is_cancelled)
        finally:
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            if ws and not ws.closed:
                await ws.close()

    async def _poll_execution(self, workflow_prompt: dict, seed: int, on_progress=None,
                               on_submitted=None, resume_prompt_id=None, is_cancelled=None) -> List[GeneratedImageInfo]:
        """Poll durable Comfy history; losing a client connection never submits twice.

        A slow generation remains active. Individual HTTP requests are bounded;
        transient failures are monitored without releasing the shared GPU worker.
        """
        if self.settings.demo_fallback:
            raise ComfyUIClientError("Demo fallback must be disabled for production generation")
        if not await self.is_healthy():
            raise ComfyUIConnectionError("ComfyUI offline; no simulated image was generated")
        try:
            prompt_id = resume_prompt_id or await self.queue_prompt(workflow_prompt)
        except (ComfyUIConnectionError, asyncio.TimeoutError) as exc:
            raise ComfyUIExecutionError(f"Uncertain execution: submission acknowledgement lost: {exc}") from exc
        if on_submitted:
            on_submitted(prompt_id)
        offline_since = None
        connection_failed_since = None
        while True:
            try:
                history = await self.get_history(prompt_id)
                if history:
                    status = history.get("status", {})
                    if status.get("status_str") == "error":
                        raise ComfyUIExecutionError(f"ComfyUI execution failed: {status.get('messages')}")
                    if status.get("completed") or history.get("outputs"):
                        return await self.collect_outputs(prompt_id, history, seed)
                session = await self.get_session()
                async with session.get(f"{self.settings.comfy_http_url}/queue",
                                       timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    resp.raise_for_status()
                    queue = await resp.json()
                connection_failed_since = None
                present = any(item[1] == prompt_id for key in ("queue_running", "queue_pending")
                              for item in queue.get(key, []))
                if not present:
                    if is_cancelled and is_cancelled():
                        raise ComfyUIExecutionError("Cancellation confirmed: original prompt absent from engine queue")
                    # History can become visible a moment after queue removal.
                    if offline_since is None:
                        offline_since = time.monotonic()
                    elif time.monotonic() - offline_since > 15:
                        raise ComfyUIExecutionError(f"Uncertain execution {prompt_id}: absent from history and queue")
                else:
                    offline_since = None
            except ComfyUIExecutionError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                logger.warning("ComfyUI unreachable while monitoring %s; retaining ownership", prompt_id)
                if connection_failed_since is None:connection_failed_since=time.monotonic()
                elif time.monotonic()-connection_failed_since>60:
                    raise ComfyUIExecutionError(f"Uncertain execution {prompt_id}: engine offline for 60s; paused until reconciliation")
            await asyncio.sleep(1)

    async def collect_outputs(self, prompt_id: str, history: dict, seed: int) -> List[GeneratedImageInfo]:
        images = []
        for output in history.get("outputs", {}).values():
            for item in output.get("images", []):
                filename = item.get("filename")
                if not filename:
                    continue
                data = await self.download_image(filename, item.get("subfolder", ""), item.get("type", "output"))
                local_name = f"out_{prompt_id[:8]}_{Path(filename).name}"
                path = self.settings.storage_outputs_dir / local_name
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(data)
                with Image.open(temporary) as im:
                    im.verify()
                temporary.replace(path)
                with Image.open(path) as im:
                    width, height = im.size
                images.append(GeneratedImageInfo(filename=local_name, url=f"/outputs/{local_name}",
                    file_path=str(path.resolve()), size_bytes=len(data), width=width, height=height, seed=seed))
        if not images:
            raise ComfyUIExecutionError("Completed workflow returned no valid images")
        return images

    def recover_local_output(self,workflow,seed,prompt_id):
        """Recover a finished PNG by exact embedded graph; never generate or guess by name."""
        import shutil
        prefixes=[Path(n.get('inputs',{}).get('filename_prefix','')).name for n in workflow.values() if n.get('class_type')=='SaveImage']
        candidates=[]
        for prefix in prefixes:
            if prefix:candidates.extend(self.settings.comfyui_output_dir.glob(prefix+'*.png'))
        for source in sorted(set(candidates),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                with Image.open(source) as im:
                    if json.loads(im.info.get('prompt','{}'))!=workflow:continue
                    width,height=im.size;im.verify()
                name=f'out_{prompt_id[:8]}_{source.name}';target=self.settings.storage_outputs_dir/name
                tmp=target.with_suffix('.tmp');shutil.copyfile(source,tmp);tmp.replace(target)
                return [GeneratedImageInfo(filename=name,url=f'/outputs/{name}',file_path=str(target.resolve()),
                    size_bytes=target.stat().st_size,width=width,height=height,seed=seed)]
            except (OSError,ValueError):continue
        return []

    async def capabilities(self) -> dict:
        session = await self.get_session()
        async with session.get(f"{self.settings.comfy_http_url}/object_info",
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            info = await resp.json()
        def choices(node, field):
            value = info.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])
            return value[0] if value and isinstance(value[0], list) else []
        settings=self.settings
        diffusion=choices("UNETLoader","unet_name")
        clips=choices("CLIPLoader","clip_name")
        vaes=choices("VAELoader","vae_name")
        needed=["ReferenceLatent","EmptyFlux2LatentImage","Flux2Scheduler","CFGGuider","SamplerCustomAdvanced"]
        klein_ready=(settings.default_klein_model in diffusion and settings.default_klein_clip in clips
                     and settings.default_klein_vae in vaes and all(n in info for n in needed))
        qwen_needed=['UnetLoaderGGUF','TextEncodeQwenImageEditPlus','ReferenceLatent','FluxKontextMultiReferenceLatentMethod',
                     'EmptySD3LatentImage','ModelSamplingAuraFlow','CFGNorm','KSampler','VAEEncodeTiled','VAEDecodeTiled']
        qwen_missing=[name for name,present in [
            (settings.default_qwen_edit_model,settings.default_qwen_edit_model in choices('UnetLoaderGGUF','unet_name')),
            (settings.default_qwen_edit_clip,settings.default_qwen_edit_clip in clips),
            (settings.default_qwen_edit_vae,settings.default_qwen_edit_vae in vaes)] if not present]
        qwen_missing.extend(n for n in qwen_needed if n not in info)
        return {"sdxl_models": choices("CheckpointLoaderSimple", "ckpt_name"),
                "flux_models": choices("UnetLoaderGGUF", "unet_name"),
                "text_encoders": choices("DualCLIPLoader", "clip_name1"),
                "vae_models": choices("VAELoader", "vae_name"),
                "character_consistency": "IPAdapterFaceID" in info,
                "controlnet_models": choices("ControlNetLoader", "control_net_name"),
                "reference_composition":klein_ready,
                "reference_engine":"FLUX.2 Klein 4B distilled" if klein_ready else None,
                "qwen_reference_composition":not qwen_missing,
                "qwen_reference_engine":"Qwen-Image-Edit-2511 GGUF Q4_K_M" if not qwen_missing else None,
                "qwen_max_references":3,
                "qwen_missing":qwen_missing,
                "klein_models":diffusion,
                "klein_text_encoders":clips,
                "klein_missing":[name for name,present in [(settings.default_klein_model,settings.default_klein_model in diffusion),
                    (settings.default_klein_clip,settings.default_klein_clip in clips),(settings.default_klein_vae,settings.default_klein_vae in vaes)] if not present],
                "simulation": False}

    # --------------------------------------------------------------------------
    # Resource Control
    # --------------------------------------------------------------------------
    async def interrupt(self) -> bool:
        """Interrupt current execution in ComfyUI."""
        try:
            session = await self.get_session()
            async with session.post(f"{self.settings.comfy_http_url}/interrupt") as resp:
                return resp.status == 200
        except Exception:
            return False

    async def free_memory(self, unload_models: bool = True, free_memory: bool = True) -> bool:
        """Trigger ComfyUI model unloading and VRAM clearing."""
        try:
            session = await self.get_session()
            payload = {"unload_models": unload_models, "free_memory": free_memory}
            async with session.post(f"{self.settings.comfy_http_url}/free", json=payload) as resp:
                return resp.status == 200
        except Exception:
            return False


_comfy_client_instance: Optional[ComfyUIClient] = None


def get_comfy_client() -> ComfyUIClient:
    global _comfy_client_instance
    if _comfy_client_instance is None:
        _comfy_client_instance = ComfyUIClient()
    return _comfy_client_instance
