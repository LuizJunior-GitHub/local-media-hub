import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore

from app.core.config import get_settings
from app.core.queue_manager import get_queue_manager
from app.engine.comfy_client import get_comfy_client
from app.engine.template_engine import get_template_engine
from app.schemas.generation import JobStatus, ModelEngine
from app.services.asset_service import get_asset_service

logger = logging.getLogger(__name__)


def create_mcp_server() -> MCPServer:
    """
    Constructs and configures the Model Context Protocol (MCP) server
    exposing local GPU image generation tools to AI agents.
    """
    settings = get_settings()
    mcp = MCPServer(
        name=settings.mcp_server_name,
        instructions=(
            "Local Media Hub (Media Forge Server): Exposes local GPU-accelerated "
            "image generation powered by AMD Radeon RX 7800 XT (16 GB GDDR6) and headless ComfyUI. "
            "Use these tools to generate photorealistic images, consistent characters, and consistent scenes."
        )
    )

    # --------------------------------------------------------------------------
    # Tool: generate_image
    # --------------------------------------------------------------------------
    @mcp.tool()
    async def generate_image(
        prompt: str,
        engine: str = "sdxl",
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        seed: Optional[int] = None,
    ) -> str:
        """
        Generate an image using SDXL (fast photorealism, 3-5s) or Flux.1 Schnell (cinematic composition, 7-25s).

        Args:
            prompt: Visual description of the image to generate.
            engine: Model engine ('sdxl' for fast photorealism or 'flux' / 'flux_schnell' for maximum quality).
            width: Image width (must be multiple of 8, e.g. 1024).
            height: Image height (must be multiple of 8, e.g. 1024).
            steps: Inference sampling steps (default 30 for SDXL, 4 for Flux).
            seed: Optional integer seed for reproducible generation.
        """
        queue_mgr = get_queue_manager()
        template_engine = get_template_engine()
        engine_clean = engine.lower().strip()

        if engine_clean in ("flux", "flux_schnell", "flux1"):
            effective_engine = ModelEngine.FLUX_SCHNELL
            flux_steps = 4 if steps == 30 else steps
            workflow, final_seed = template_engine.build_flux_workflow(
                prompt=prompt,
                seed=seed,
                width=width,
                height=height,
                steps=flux_steps,
            )
            meta = {"engine": "flux_schnell", "steps": flux_steps}
        else:
            effective_engine = ModelEngine.SDXL
            workflow, final_seed = template_engine.build_sdxl_workflow(
                prompt=prompt,
                seed=seed,
                width=width,
                height=height,
                steps=steps,
            )
            meta = {"engine": "sdxl", "steps": steps}

        job_id = await queue_mgr.submit_job(
            engine=effective_engine,
            workflow=workflow,
            seed=final_seed,
            prompt=prompt,
            metadata=meta,
        )

        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            return f"❌ Image generation failed: {job.error}"

        if not job.images:
            return "❌ Job completed but no images were returned."

        first_img = job.images[0]
        return (
            f"✅ **Image Generated Successfully**\n"
            f"- **Engine**: `{job.engine.value}`\n"
            f"- **Seed**: `{first_img.seed}`\n"
            f"- **Execution Time**: {job.execution_time_seconds}s\n"
            f"- **Dimensions**: {first_img.width}x{first_img.height}\n"
            f"- **Local Path**: `{first_img.file_path}`\n"
            f"- **Web URL**: `{first_img.url}`\n"
        )

    # --------------------------------------------------------------------------
    # Tool: generate_character_image
    # --------------------------------------------------------------------------
    @mcp.tool()
    async def generate_character_image(
        character_id: str,
        prompt: str,
        face_strength: float = 0.85,
        seed: Optional[int] = None,
    ) -> str:
        """
        Generate an image of a registered biblical or historical character while strictly preserving
        facial identity using IP-Adapter FaceID.

        Args:
            character_id: Slug ID of the registered anchor character (e.g. 'moises', 'davi').
            prompt: Scene description describing the action, costume, atmosphere, or context.
            face_strength: Facial adherence strength (0.1 to 1.0, default 0.85).
            seed: Optional seed for reproducibility.
        """
        asset_service = get_asset_service()
        queue_mgr = get_queue_manager()
        template_engine = get_template_engine()
        comfy_client = get_comfy_client()

        anchor = asset_service.get_character(character_id)
        if not anchor:
            available = [c.id for c in asset_service.list_characters()]
            return (
                f"❌ Character anchor '{character_id}' was not found. "
                f"Available characters: {', '.join(available) if available else 'None registered yet.'}"
            )

        # Sync reference image to ComfyUI
        uploaded_name = await comfy_client.upload_image(Path(anchor.file_path))

        full_prompt = prompt
        if anchor.default_prompt_modifier:
            full_prompt = f"{prompt}, {anchor.default_prompt_modifier}"

        workflow, final_seed = template_engine.build_character_workflow(
            anchor_image_filename=uploaded_name,
            prompt=full_prompt,
            face_strength=face_strength,
            seed=seed,
        )

        job_id = await queue_mgr.submit_job(
            engine=ModelEngine.CHARACTER_CONSISTENCY,
            workflow=workflow,
            seed=final_seed,
            prompt=full_prompt,
            metadata={"character_id": character_id, "character_name": anchor.name},
        )

        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            return f"❌ Character generation failed: {job.error}"

        if not job.images:
            return "❌ Job completed but no images were returned."

        first_img = job.images[0]
        return (
            f"✅ **Consistent Character Image Generated**\n"
            f"- **Character**: {anchor.name} (`{anchor.id}`)\n"
            f"- **Face Strength**: {face_strength}\n"
            f"- **Seed**: `{first_img.seed}`\n"
            f"- **Execution Time**: {job.execution_time_seconds}s\n"
            f"- **Local Path**: `{first_img.file_path}`\n"
            f"- **Web URL**: `{first_img.url}`\n"
        )

    # --------------------------------------------------------------------------
    # Tool: generate_location_image
    # --------------------------------------------------------------------------
    @mcp.tool()
    async def generate_location_image(
        location_id: str,
        prompt: str,
        depth_strength: float = 0.60,
        seed: Optional[int] = None,
    ) -> str:
        """
        Generate an image of a registered location or scene while preserving architectural depth
        geometry and atmosphere using ControlNet Depth and style transfer.

        Args:
            location_id: Slug ID of the registered anchor location (e.g. 'templo_salomao', 'monte_sinai').
            prompt: Action or scene taking place at this location.
            depth_strength: Structural depth adherence (0.0 to 1.0, default 0.60).
            seed: Optional seed for reproducibility.
        """
        asset_service = get_asset_service()
        queue_mgr = get_queue_manager()
        template_engine = get_template_engine()
        comfy_client = get_comfy_client()

        anchor = asset_service.get_location(location_id)
        if not anchor:
            available = [l.id for l in asset_service.list_locations()]
            return (
                f"❌ Location anchor '{location_id}' was not found. "
                f"Available locations: {', '.join(available) if available else 'None registered yet.'}"
            )

        uploaded_name = await comfy_client.upload_image(Path(anchor.file_path))

        full_prompt = prompt
        if anchor.style_notes:
            full_prompt = f"{prompt}, {anchor.style_notes}"

        workflow, final_seed = template_engine.build_location_workflow(
            anchor_image_filename=uploaded_name,
            prompt=full_prompt,
            depth_strength=depth_strength,
            seed=seed,
        )

        job_id = await queue_mgr.submit_job(
            engine=ModelEngine.LOCATION_CONSISTENCY,
            workflow=workflow,
            seed=final_seed,
            prompt=full_prompt,
            metadata={"location_id": location_id, "location_name": anchor.name},
        )

        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            return f"❌ Location generation failed: {job.error}"

        if not job.images:
            return "❌ Job completed but no images were returned."

        first_img = job.images[0]
        return (
            f"✅ **Consistent Location Image Generated**\n"
            f"- **Location**: {anchor.name} (`{anchor.id}`)\n"
            f"- **Depth Adherence**: {depth_strength}\n"
            f"- **Seed**: `{first_img.seed}`\n"
            f"- **Execution Time**: {job.execution_time_seconds}s\n"
            f"- **Local Path**: `{first_img.file_path}`\n"
            f"- **Web URL**: `{first_img.url}`\n"
        )

    # --------------------------------------------------------------------------
    # Tool: list_character_anchors
    # --------------------------------------------------------------------------
    @mcp.tool()
    def list_character_anchors() -> str:
        """List all registered character anchor identities available for consistent generation."""
        asset_service = get_asset_service()
        chars = asset_service.list_characters()
        if not chars:
            return "No character anchors are currently registered in storage/references/characters."

        lines = ["### Registered Character Anchors:"]
        for c in chars:
            desc = f" - {c.description}" if c.description else ""
            lines.append(f"- **`{c.id}`**: {c.name}{desc} (Image: `{c.image_filename}`)")
        return "\n".join(lines)

    # --------------------------------------------------------------------------
    # Tool: list_location_anchors
    # --------------------------------------------------------------------------
    @mcp.tool()
    def list_location_anchors() -> str:
        """List all registered location and scenery anchors available for consistent generation."""
        asset_service = get_asset_service()
        locs = asset_service.list_locations()
        if not locs:
            return "No location anchors are currently registered in storage/references/locations."

        lines = ["### Registered Location Anchors:"]
        for l in locs:
            desc = f" - {l.description}" if l.description else ""
            lines.append(f"- **`{l.id}`**: {l.name}{desc} (Image: `{l.image_filename}`)")
        return "\n".join(lines)

    # --------------------------------------------------------------------------
    # Tool: get_system_status
    # --------------------------------------------------------------------------
    @mcp.tool()
    async def get_system_status() -> str:
        """Check status of AMD GPU, ComfyUI backend connection, and job queue."""
        comfy_client = get_comfy_client()
        queue_mgr = get_queue_manager()
        settings = get_settings()

        comfy_online = await comfy_client.is_healthy()
        stats = queue_mgr.get_stats()

        return (
            f"### Local Media Hub System Status\n"
            f"- **GPU Hardware**: {settings.gpu_device_name} ({settings.gpu_vram_gb} GB VRAM, {settings.gpu_backend})\n"
            f"- **GPU Concurrency Lock**: {'Locked (Active Inference)' if stats['gpu_locked'] else 'Available'}\n"
            f"- **Headless ComfyUI**: {'🟢 Online' if comfy_online else '🔴 Offline / Not reachable'}\n"
            f"- **Queue Length**: {stats['queue_length']} jobs pending\n"
            f"- **Total Jobs Completed**: {stats['total_completed']}\n"
            f"- **Total Jobs Failed**: {stats['total_failed']}\n"
        )

    return mcp


async def run_mcp_stdio_server() -> None:
    """Run the MCP server over STDIO for Claude Desktop or local agents."""
    queue_mgr = get_queue_manager()
    await queue_mgr.start()
    try:
        mcp = create_mcp_server()
        await mcp.run_stdio_async()
    finally:
        await queue_mgr.stop()


if __name__ == "__main__":
    asyncio.run(run_mcp_stdio_server())
