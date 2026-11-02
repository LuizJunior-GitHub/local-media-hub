"""MCP stdio client for the ONE running HTTP hub. No GPU worker in this process."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse
import httpx
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

BASE_URL = os.environ.get("LOCAL_MEDIA_HUB_URL", "http://127.0.0.1:8000")
if urlparse(BASE_URL).hostname not in ("127.0.0.1", "localhost", "::1"):
    raise ValueError("Local Media Hub MCP accepts only loopback servers")

@asynccontextmanager
async def lifespan(server):
    # Startup occurs in tools, so initialize/tool discovery stays fast.
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20, trust_env=False) as client:
        server.hub_client = client
        yield {}

async def request(server, method, path, payload=None):
    if path in ("/api/v1/system/status", "/api/v1/system/capabilities"):
        from scripts.ensure_services import ensure_services
        await asyncio.to_thread(ensure_services)
    client = getattr(server, "hub_client", None)
    if client is None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=20, trust_env=False) as client:
            response = await client.request(method, path, json=payload)
    else:
        response = await client.request(method, path, json=payload)
    if response.is_error:
        raise ValueError(f"Hub HTTP {response.status_code}: {response.text[:1500]}")
    return response.json()

def create_mcp_server():
    server = MCPServer(name="local-media-hub", lifespan=lifespan, instructions=(
        "Generate real images on the local PC, without paid image APIs. Submit returns job_id immediately. "
        "Poll get_job; never resubmit after a timeout. Use a stable request_id and explicit seed for idempotency. "
        "Check capabilities first. Missing reference conditioning must never be replaced silently with text-only generation. "
        "A completed PNG path can be shown inline. Native 1024x576 or 1280x720 are 16:9; 1920x1080 exports are resized."))

    @server.tool()
    async def get_system_status() -> dict:
        """Check the shared queue, local engine and GPU status."""
        return await request(server, "GET", "/api/v1/system/status")

    @server.tool()
    async def get_capabilities() -> dict:
        """List actually installed models and reference conditioning support."""
        return await request(server, "GET", "/api/v1/system/capabilities")

    @server.tool()
    async def generate_image(prompt: str, request_id: str, seed: int, engine: str = "flux",
                             width: int = 1024, height: int = 576, steps: int = 4) -> dict:
        """Enqueue a real local image. Returns job_id immediately; then use get_job.

        request_id must stay unchanged on transport retries; seed is explicit.
        engine: flux (1-12 steps) or sdxl (10-60 steps).
        """
        if engine not in ("flux", "sdxl"):
            raise ValueError("engine must be flux or sdxl")
        return await request(server, "POST", f"/api/v1/generate/{engine}",
            {"prompt":prompt,"request_id":request_id,"seed":seed,"width":width,"height":height,
             "steps":steps,"wait_for_completion":False})

    @server.tool()
    async def get_job(job_id: str) -> dict:
        """Read durable status and PNG paths without generating again."""
        return await request(server,"GET",f"/api/v1/jobs/{quote(job_id, safe='')}")

    @server.tool()
    async def list_jobs(limit: int = 20) -> dict:
        """List recent durable jobs, including failures and uncertain executions."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1-100")
        return await request(server,"GET",f"/api/v1/jobs?limit={limit}")

    @server.tool()
    async def reconcile_job(job_id: str) -> dict:
        """Recover an uncertain original execution from Comfy history; never resubmit."""
        return await request(server,"POST",f"/api/v1/jobs/{quote(job_id, safe='')}/reconcile")

    @server.tool()
    async def cancel_job(job_id: str) -> dict:
        """Request interruption of this hub job; confirm terminal status with get_job."""
        return await request(server,"POST",f"/api/v1/jobs/{quote(job_id, safe='')}/cancel")

    @server.tool()
    async def list_anchors() -> dict:
        """Read registered local character and location anchors."""
        return await request(server,"GET","/api/v1/assets")

    @server.tool()
    async def import_quiz_manifest(manifest: dict, project_id: str) -> dict:
        """Validate and persist a Flow quiz manifest, with four slots per kit. Does not generate."""
        return await request(server,"POST","/api/v1/quiz/import",{"manifest":manifest,"project_id":project_id})

    @server.tool()
    async def generate_quiz_slot(project_id: str, source_index: str, seed: int,
                                 engine: str = "klein", retry_failed: bool = False, attempt_id: str = "initial",regenerate: bool = False) -> dict:
        """Submit one quiz scene using its approved character/place/object images. Klein applies real multi-reference latents.
        Completed jobs are preserved; explicit regenerate=True with a NEW stable attempt_id creates a variation.
        Failed jobs require retry_failed=True and a new attempt_id. Missing/unapproved references block only that scene.
        """
        return await request(server,"POST","/api/v1/quiz/generate",
            {"project_id":project_id,"source_index":source_index,"seed":seed,"engine":engine,
             "retry_failed":retry_failed,"attempt_id":attempt_id,"regenerate":regenerate})

    @server.tool()
    async def get_quiz_project(project_id:str)->dict:
        """Read saved quiz, reference shots/approvals, four slots, progress, stale results and batch state."""
        return await request(server,'GET',f'/api/v1/production/{quote(project_id,safe="")}')

    @server.tool()
    async def generate_quiz_reference(project_id:str,reference_id:str,seed:int,attempt_id:str='initial')->dict:
        """Generate a character, place or object reference. First shot auto-approves only if manual review is off.
        A new variation needs a new stable attempt_id. Existing approved images stay preserved.
        """
        return await request(server,'POST',f'/api/v1/production/{quote(project_id,safe="")}/references/generate',
            {'reference_id':reference_id,'seed':seed,'attempt_id':attempt_id})

    @server.tool()
    async def approve_quiz_reference(project_id:str,reference_id:str,shot_id:str)->dict:
        """Approve one actual local shot for reuse in dependent scenes; preserves completed images and marks old dependencies stale."""
        return await request(server,'POST',f'/api/v1/production/{quote(project_id,safe="")}/references/approve',
            {'reference_id':reference_id,'shot_id':shot_id})

    @server.tool()
    async def import_quiz_reference_image(project_id:str,reference_id:str,image_path:str,approve_image:bool=False)->dict:
        """Reuse a local PNG/JPEG/WebP as a quiz reference. Uploads only to loopback hub; approval is explicit."""
        from pathlib import Path
        from PIL import Image
        path=Path(image_path).resolve()
        if path.suffix.lower() not in ['.png','.jpg','.jpeg','.webp'] or path.stat().st_size>25*1024*1024:
            raise ValueError('Use an image file under 25 MB')
        with Image.open(path) as im:im.verify()
        async with httpx.AsyncClient(base_url=BASE_URL,timeout=20,trust_env=False) as client:
            with path.open('rb') as image_file:
                response=await client.post(f'/api/v1/production/{quote(project_id,safe="")}/references/upload',
                    data={'reference_id':reference_id,'approve_image':str(approve_image).lower()},files={'file':(path.name,image_file)})
            response.raise_for_status();return response.json()

    @server.tool()
    async def configure_quiz_project(project_id:str,changes:dict)->dict:
        """Set manual_review/use_same_background/engine or global_selection P,R,P_bg,R_bg. Preserves completed results."""
        return await request(server,'PATCH',f'/api/v1/production/{quote(project_id,safe="")}/settings',{'changes':changes})

    @server.tool()
    async def edit_quiz_item(project_id:str,target:str,item_id:str,changes:dict)->dict:
        """Edit slot/reference prompts. Character references support visual_profile (context,apparent_age,skin,face,eyes,hair,facial_hair,build,clothing,distinctive_features,avoid) and clear_approval.
        Profile changes require new image approval, preserve prior images, and compose explicit identity portrait prompts.
        Slots support reference_ids, reviewed and selection_override. changes.expected detects concurrent field edits.
        """
        return await request(server,'PATCH',f'/api/v1/production/{quote(project_id,safe="")}/{quote(target,safe="")}/{quote(item_id,safe="")}',{'changes':changes})

    @server.tool()
    async def start_quiz_batch(project_id:str,phase:str='all',source_indexes:list[str]|None=None)->dict:
        """Start/resume serial references and scenes; stages all/references/scenes. Auto-approve only first references if manual review off.
        Skips completed/disabled jobs, no automatic retry. Returns immediately; monitor get_quiz_project.
        """
        return await request(server,'POST',f'/api/v1/production/{quote(project_id,safe="")}/batch/start',{'phase':phase,'source_indexes':source_indexes})

    @server.tool()
    async def pause_quiz_batch(project_id:str)->dict:
        """Pause new starts; current submitted job may finish and stays recorded. Resume does not regenerate completed work."""
        return await request(server,'POST',f'/api/v1/production/{quote(project_id,safe="")}/batch/pause')

    @server.tool()
    async def export_quiz_project(project_id: str) -> dict:
        """Copy completed images to 001P.png/001R.png/001P_bg.png/001R_bg.png and write a production index."""
        return await request(server,"POST",f"/api/v1/quiz/{quote(project_id,safe='')}/export")

    return server

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(create_mcp_server().run_stdio_async())
