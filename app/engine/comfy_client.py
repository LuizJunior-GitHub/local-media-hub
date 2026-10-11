import asyncio
import json
import logging
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
            timeout = aiohttp.ClientTimeout(total=self.settings.comfyui_timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
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
    async def execute_and_wait(
        self,
        workflow_prompt: dict,
        seed: int,
        on_progress: Optional[Callable[[int, int], Any]] = None,
    ) -> List[GeneratedImageInfo]:
        """
        Execute workflow via ComfyUI, monitor progress in real-time via WebSocket,
        and retrieve & save generated images to local storage/outputs/.
        """
        if not await self.is_healthy():
            if self.settings.demo_fallback:
                logger.warning("ComfyUI is offline. Executing generation in local Demo/Simulation Mode.")
                return await self._simulate_generation(workflow_prompt, seed, on_progress)
            raise ComfyUIConnectionError(f"Could not connect to ComfyUI at {self.settings.comfy_http_url}")

        session = await self.get_session()
        ws_url = self.settings.comfy_ws_url

        try:
            ws = await session.ws_connect(ws_url)
        except Exception as e:
            logger.warning(f"Could not open WebSocket to ComfyUI ({e}). Falling back to polling mode.")
            ws = None

        prompt_id = await self.queue_prompt(workflow_prompt)
        logger.info(f"Queued ComfyUI job prompt_id: {prompt_id}")

        generated_images: List[GeneratedImageInfo] = []

        try:
            if ws is not None:
                # WebSocket listener mode
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(msg.data)
                        except Exception:
                            continue

                        event_type = event.get("type")
                        data = event.get("data", {})

                        if event_type == "progress":
                            # Current step progress
                            val = data.get("value", 0)
                            max_val = data.get("max", 1)
                            if on_progress:
                                try:
                                    res = on_progress(val, max_val)
                                    if asyncio.iscoroutine(res):
                                        await res
                                except Exception as cb_err:
                                    logger.debug(f"Progress callback exception: {cb_err}")

                        elif event_type == "execution_error":
                            if data.get("prompt_id") == prompt_id:
                                exc_msg = data.get("exception_message", "Unknown error")
                                node_id = data.get("node_id", "unknown")
                                raise ComfyUIExecutionError(f"Node {node_id} failed: {exc_msg}")

                        elif event_type == "executing":
                            # When executing node is None and prompt_id matches, run is completed
                            current_node = data.get("node")
                            msg_prompt_id = data.get("prompt_id")
                            if current_node is None and msg_prompt_id == prompt_id:
                                logger.info(f"Execution finished for prompt_id: {prompt_id}")
                                break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            else:
                # Polling fallback mode
                poll_timeout = self.settings.comfyui_timeout_seconds
                start_time = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - start_time < poll_timeout:
                    history = await self.get_history(prompt_id)
                    if history is not None:
                        break
                    await asyncio.sleep(0.5)

        finally:
            if ws and not ws.closed:
                await ws.close()

        # Retrieve outputs from history
        history = await self.get_history(prompt_id)
        if not history:
            # Short wait if history is briefly lagging
            for _ in range(10):
                await asyncio.sleep(0.5)
                history = await self.get_history(prompt_id)
                if history:
                    break

        if not history:
            raise ComfyUIExecutionError(f"Execution completed but no history found for prompt_id {prompt_id}")

        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img_data in node_output["images"]:
                    remote_filename = img_data.get("filename")
                    subfolder = img_data.get("subfolder", "")
                    folder_type = img_data.get("type", "output")

                    if not remote_filename:
                        continue

                    # Download image bytes
                    img_bytes = await self.download_image(remote_filename, subfolder, folder_type)

                    # Save to local storage/outputs
                    local_filename = f"out_{prompt_id[:8]}_{remote_filename}"
                    local_path = self.settings.storage_outputs_dir / local_filename
                    with open(local_path, "wb") as f:
                        f.write(img_bytes)

                    # Inspect dimensions
                    width, height = 1024, 1024
                    try:
                        with Image.open(local_path) as im:
                            width, height = im.width, im.height
                    except Exception:
                        pass

                    info = GeneratedImageInfo(
                        filename=local_filename,
                        subfolder=subfolder,
                        url=f"/outputs/{local_filename}",
                        file_path=str(local_path.resolve()),
                        content_type="image/png",
                        size_bytes=len(img_bytes),
                        width=width,
                        height=height,
                        seed=seed,
                    )
                    generated_images.append(info)

        if not generated_images:
            raise ComfyUIExecutionError("No output images were generated by the workflow")

        return generated_images

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

    async def _simulate_generation(
        self,
        workflow_prompt: dict,
        seed: int,
        on_progress: Optional[Callable[[int, int], Any]] = None,
    ) -> List[GeneratedImageInfo]:
        """
        Simulate GPU inference pipeline when ComfyUI is offline.
        Executes step progression with WebSockets and generates an illustrative test image.
        """
        import time
        from PIL import ImageDraw

        prompt_text = "Biblical portrait scene"
        width, height = 1024, 1024
        steps = 30
        anchor_filename = None

        for node_id, node in workflow_prompt.items():
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})
            if class_type == "EmptyLatentImage":
                width = int(inputs.get("width", 1024))
                height = int(inputs.get("height", 1024))
            elif class_type == "KSampler":
                steps = int(inputs.get("steps", 30))
            elif class_type == "LoadImage":
                anchor_filename = inputs.get("image")
            elif class_type == "CLIPTextEncode":
                t = inputs.get("text", "")
                if not any(neg in t.lower() for neg in ["ugly", "deformed", "bad anatomy", "watermark", "low quality"]):
                    prompt_text = t

        # Simulate step progression with realistic timing
        total_time = 2.2
        sleep_per_step = max(0.02, total_time / max(steps, 1))

        for step in range(1, steps + 1):
            await asyncio.sleep(sleep_per_step)
            if on_progress:
                try:
                    res = on_progress(step, steps)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        # Generate realistic illustrative test canvas with Pillow
        img = Image.new("RGB", (width, height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Draw decorative background gradient
        for y in range(height):
            ratio = y / height
            r = int(18 + ratio * 80)
            g = int(24 + ratio * 60)
            b = int(48 + ratio * 30)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # Check if an anchor image exists to composite in center
        anchor_loaded = False
        if anchor_filename:
            char_path = self.settings.storage_characters_dir / anchor_filename
            loc_path = self.settings.storage_locations_dir / anchor_filename
            target_path = char_path if char_path.exists() else (loc_path if loc_path.exists() else None)
            if target_path:
                try:
                    with Image.open(target_path) as ref_img:
                        ref_resized = ref_img.resize((int(width * 0.50), int(height * 0.50)))
                        paste_x = int((width - ref_resized.width) / 2)
                        paste_y = int((height - ref_resized.height) / 2) - 20
                        draw.rectangle(
                            [paste_x - 6, paste_y - 6, paste_x + ref_resized.width + 6, paste_y + ref_resized.height + 6],
                            fill=(34, 197, 94),
                        )
                        img.paste(ref_resized, (paste_x, paste_y))
                        anchor_loaded = True
                except Exception:
                    pass

        if not anchor_loaded:
            cx, cy = width // 2, int(height * 0.42)
            for radius in range(200, 40, -20):
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=(234, 179, 8), width=3)
            draw.ellipse([cx - 50, cy - 50, cx + 50, cy + 50], fill=(250, 204, 21))

        # Top banner & border
        draw.rectangle([20, 20, width - 20, height - 20], outline=(34, 197, 94), width=4)
        draw.rectangle([20, 20, width - 20, 70], fill=(15, 23, 42))
        draw.text((36, 32), "LOCAL MEDIA HUB • TEST INFERENCE PIPELINE (MOCK ENGINE)", fill=(34, 197, 94))

        # Bottom info card
        draw.rectangle([20, height - 120, width - 20, height - 20], fill=(9, 13, 22))
        clean_prompt = (prompt_text[:110] + "...") if len(prompt_text) > 110 else prompt_text
        draw.text((36, height - 105), f"PROMPT: {clean_prompt}", fill=(241, 245, 249))
        draw.text(
            (36, height - 60),
            f"SEED: {seed}   |   STEPS: {steps}   |   SIZE: {width}x{height}   |   STATUS: OK",
            fill=(148, 163, 184),
        )

        out_filename = f"test_out_{int(time.time())}_{seed}.png"
        out_path = self.settings.storage_outputs_dir / out_filename
        img.save(out_path, format="PNG")

        return [
            GeneratedImageInfo(
                filename=out_filename,
                subfolder="",
                url=f"/outputs/{out_filename}",
                file_path=str(out_path.resolve()),
                content_type="image/png",
                size_bytes=out_path.stat().st_size,
                width=width,
                height=height,
                seed=seed,
            )
        ]


_comfy_client_instance: Optional[ComfyUIClient] = None


def get_comfy_client() -> ComfyUIClient:
    global _comfy_client_instance
    if _comfy_client_instance is None:
        _comfy_client_instance = ComfyUIClient()
    return _comfy_client_instance
