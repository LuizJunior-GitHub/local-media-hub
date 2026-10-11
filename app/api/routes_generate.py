from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from app.core.queue_manager import QueueManager, get_queue_manager
from app.engine.comfy_client import ComfyUIClient, get_comfy_client
from app.engine.template_engine import TemplateEngine, get_template_engine
from app.schemas.generation import (
    CharacterGenerateRequest,
    FluxSchnellGenerateRequest,
    GenerationJobResponse,
    JobListResponse,
    JobStatus,
    LocationGenerateRequest,
    ModelEngine,
    SDXLGenerateRequest,
)
from app.services.asset_service import AssetService, get_asset_service

router = APIRouter(prefix="/api/v1", tags=["Generation"])


# ------------------------------------------------------------------------------
# 1. SDXL Standard Generation
# ------------------------------------------------------------------------------
@router.post(
    "/generate/sdxl",
    response_model=GenerationJobResponse,
    summary="Generate image with SDXL (Juggernaut XL / RealVisXL)",
    description="Optimized for rapid generation (3-5s on RX 7800 XT) with rich photorealism and biblical aesthetics."
)
async def generate_sdxl(
    req: SDXLGenerateRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    template_engine: TemplateEngine = Depends(get_template_engine),
):
    workflow, seed = template_engine.build_sdxl_workflow(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        seed=req.seed,
        width=req.width,
        height=req.height,
        steps=req.steps,
        cfg_scale=req.cfg_scale,
        sampler_name=req.sampler_name,
        scheduler=req.scheduler,
        checkpoint=req.checkpoint,
    )

    metadata = {
        "engine": "sdxl",
        "steps": req.steps,
        "cfg_scale": req.cfg_scale,
        "width": req.width,
        "height": req.height,
        "sampler": req.sampler_name,
    }

    job_id = await queue_mgr.submit_job(
        engine=ModelEngine.SDXL,
        workflow=workflow,
        seed=seed,
        prompt=req.prompt,
        metadata=metadata,
    )

    if req.wait_for_completion:
        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            raise HTTPException(status_code=500, detail=f"Generation failed: {job.error}")
        return job

    job = queue_mgr.get_job(job_id)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=job.model_dump(mode="json"))


# ------------------------------------------------------------------------------
# 2. Flux.1 Schnell Generation
# ------------------------------------------------------------------------------
@router.post(
    "/generate/flux",
    response_model=GenerationJobResponse,
    summary="Generate cinematic image with Flux.1 Schnell (GGUF quant)",
    description="Maximum quality, complex compositions, and typography (7-25s on RX 7800 XT)."
)
async def generate_flux(
    req: FluxSchnellGenerateRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    template_engine: TemplateEngine = Depends(get_template_engine),
):
    workflow, seed = template_engine.build_flux_workflow(
        prompt=req.prompt,
        seed=req.seed,
        width=req.width,
        height=req.height,
        steps=req.steps,
        guidance=req.guidance,
        sampler_name=req.sampler_name,
        scheduler=req.scheduler,
    )

    metadata = {
        "engine": "flux_schnell",
        "steps": req.steps,
        "guidance": req.guidance,
        "width": req.width,
        "height": req.height,
    }

    job_id = await queue_mgr.submit_job(
        engine=ModelEngine.FLUX_SCHNELL,
        workflow=workflow,
        seed=seed,
        prompt=req.prompt,
        metadata=metadata,
    )

    if req.wait_for_completion:
        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            raise HTTPException(status_code=500, detail=f"Generation failed: {job.error}")
        return job

    job = queue_mgr.get_job(job_id)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=job.model_dump(mode="json"))


# ------------------------------------------------------------------------------
# 3. Consistent Character Generation (IP-Adapter FaceID)
# ------------------------------------------------------------------------------
@router.post(
    "/generate/character",
    response_model=GenerationJobResponse,
    summary="Generate image with Character Consistency (IP-Adapter FaceID)",
    description="Preserves facial identity of an anchor character across distinct prompts and camera angles."
)
async def generate_character(
    req: CharacterGenerateRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    template_engine: TemplateEngine = Depends(get_template_engine),
    comfy_client: ComfyUIClient = Depends(get_comfy_client),
    asset_service: AssetService = Depends(get_asset_service),
):
    anchor = asset_service.get_character(req.character_id)
    if not anchor:
        raise HTTPException(
            status_code=404,
            detail=f"Character anchor '{req.character_id}' not found. Register it via /api/v1/assets/characters first."
        )

    # Ensure anchor image is uploaded into ComfyUI's input directory
    anchor_path = Path(anchor.file_path)
    try:
        uploaded_name = await comfy_client.upload_image(anchor_path)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to synchronize anchor image with ComfyUI: {e}"
        )

    full_prompt = req.prompt
    if anchor.default_prompt_modifier:
        full_prompt = f"{req.prompt}, {anchor.default_prompt_modifier}"

    workflow, seed = template_engine.build_character_workflow(
        anchor_image_filename=uploaded_name,
        prompt=full_prompt,
        negative_prompt=req.negative_prompt,
        face_strength=req.face_strength,
        seed=req.seed,
        width=req.width,
        height=req.height,
        steps=req.steps,
        cfg_scale=req.cfg_scale,
        sampler_name=req.sampler_name,
        scheduler=req.scheduler,
    )

    metadata = {
        "engine": "character_consistency",
        "character_id": req.character_id,
        "character_name": anchor.name,
        "face_strength": req.face_strength,
    }

    job_id = await queue_mgr.submit_job(
        engine=ModelEngine.CHARACTER_CONSISTENCY,
        workflow=workflow,
        seed=seed,
        prompt=full_prompt,
        metadata=metadata,
    )

    if req.wait_for_completion:
        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            raise HTTPException(status_code=500, detail=f"Generation failed: {job.error}")
        return job

    job = queue_mgr.get_job(job_id)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=job.model_dump(mode="json"))


# ------------------------------------------------------------------------------
# 4. Consistent Location Generation (ControlNet Depth & Style)
# ------------------------------------------------------------------------------
@router.post(
    "/generate/location",
    response_model=GenerationJobResponse,
    summary="Generate image with Location Consistency (ControlNet Depth)",
    description="Preserves architectural depth geometry and visual aesthetic of a fixed location."
)
async def generate_location(
    req: LocationGenerateRequest,
    queue_mgr: QueueManager = Depends(get_queue_manager),
    template_engine: TemplateEngine = Depends(get_template_engine),
    comfy_client: ComfyUIClient = Depends(get_comfy_client),
    asset_service: AssetService = Depends(get_asset_service),
):
    anchor = asset_service.get_location(req.location_id)
    if not anchor:
        raise HTTPException(
            status_code=404,
            detail=f"Location anchor '{req.location_id}' not found. Register it via /api/v1/assets/locations first."
        )

    # Ensure anchor image is uploaded into ComfyUI's input directory
    anchor_path = Path(anchor.file_path)
    try:
        uploaded_name = await comfy_client.upload_image(anchor_path)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to synchronize location image with ComfyUI: {e}"
        )

    full_prompt = req.prompt
    if anchor.style_notes:
        full_prompt = f"{req.prompt}, {anchor.style_notes}"

    workflow, seed = template_engine.build_location_workflow(
        anchor_image_filename=uploaded_name,
        prompt=full_prompt,
        negative_prompt=req.negative_prompt,
        depth_strength=req.depth_strength,
        seed=req.seed,
        width=req.width,
        height=req.height,
        steps=req.steps,
        cfg_scale=req.cfg_scale,
        sampler_name=req.sampler_name,
        scheduler=req.scheduler,
    )

    metadata = {
        "engine": "location_consistency",
        "location_id": req.location_id,
        "location_name": anchor.name,
        "depth_strength": req.depth_strength,
    }

    job_id = await queue_mgr.submit_job(
        engine=ModelEngine.LOCATION_CONSISTENCY,
        workflow=workflow,
        seed=seed,
        prompt=full_prompt,
        metadata=metadata,
    )

    if req.wait_for_completion:
        job = await queue_mgr.wait_for_job(job_id)
        if job.status == JobStatus.FAILED:
            raise HTTPException(status_code=500, detail=f"Generation failed: {job.error}")
        return job

    job = queue_mgr.get_job(job_id)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=job.model_dump(mode="json"))


# ------------------------------------------------------------------------------
# 5. Job Status & Management
# ------------------------------------------------------------------------------
@router.get(
    "/jobs/{job_id}",
    response_model=GenerationJobResponse,
    summary="Get generation job status and output images"
)
async def get_job_status(
    job_id: str,
    queue_mgr: QueueManager = Depends(get_queue_manager)
):
    job = queue_mgr.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List recent generation jobs"
)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    queue_mgr: QueueManager = Depends(get_queue_manager)
):
    jobs = queue_mgr.list_jobs(limit=limit)
    return JobListResponse(jobs=jobs, total=len(jobs))


@router.post(
    "/jobs/{job_id}/cancel",
    summary="Cancel a queued or active generation job"
)
async def cancel_job(
    job_id: str,
    queue_mgr: QueueManager = Depends(get_queue_manager)
):
    cancelled = await queue_mgr.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail=f"Could not cancel job {job_id} (already completed or not found)")
    return {"status": "cancelled", "job_id": job_id}


@router.get(
    "/gallery",
    summary="List all generated images stored on disk"
)
async def get_gallery(limit: int = Query(default=30, ge=1, le=100)):
    from datetime import datetime, timezone
    from app.core.config import get_settings
    settings = get_settings()
    out_dir = settings.storage_outputs_dir
    images = []
    if out_dir.exists():
        files = sorted(
            [p for p in out_dir.glob("*.png") if not p.name.startswith("test_out_")],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        for p in files[:limit]:
            stat = p.stat()
            engine = "FLUX" if "FLUX" in p.name else "SDXL"
            images.append({
                "filename": p.name,
                "url": f"/outputs/{p.name}",
                "file_path": str(p),
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "engine": engine
            })
    return {"images": images, "total": len(images)}


# ------------------------------------------------------------------------------
# 6. WebSocket Real-time Progress Stream
# ------------------------------------------------------------------------------
@router.websocket("/jobs/{job_id}/ws")
async def websocket_job_progress(websocket: WebSocket, job_id: str):
    queue_mgr = get_queue_manager()
    job = queue_mgr.get_job(job_id)
    if not job:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Job not found")
        return

    await websocket.accept()
    sub_queue = queue_mgr.subscribe(job_id)

    # Send initial state
    await websocket.send_text(job.model_dump_json())

    try:
        while True:
            updated_job: GenerationJobResponse = await sub_queue.get()
            await websocket.send_text(updated_job.model_dump_json())
            if updated_job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                break
    except WebSocketDisconnect:
        pass
    finally:
        queue_mgr.unsubscribe(job_id, sub_queue)
