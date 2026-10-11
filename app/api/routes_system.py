from typing import Any, Dict
from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.queue_manager import QueueManager, get_queue_manager
from app.engine.comfy_client import ComfyUIClient, get_comfy_client

router = APIRouter(prefix="/api/v1/system", tags=["System & GPU"])


@router.get("/health", summary="Overall service health check")
async def health_check(
    settings: Settings = Depends(get_settings),
    comfy_client: ComfyUIClient = Depends(get_comfy_client),
    queue_mgr: QueueManager = Depends(get_queue_manager),
) -> Dict[str, Any]:
    comfy_online = await comfy_client.is_healthy()
    queue_stats = queue_mgr.get_stats()

    status_str = "healthy" if comfy_online else "degraded"

    return {
        "status": status_str,
        "service": settings.app_name,
        "environment": settings.app_env,
        "comfyui_online": comfy_online,
        "comfyui_endpoint": settings.comfy_http_url,
        "gpu_target": settings.gpu_device_name,
        "gpu_vram_gb": settings.gpu_vram_gb,
        "gpu_backend": settings.gpu_backend,
        "queue": queue_stats,
    }


@router.get("/status", summary="Detailed hardware, GPU and ComfyUI status")
async def system_status(
    settings: Settings = Depends(get_settings),
    comfy_client: ComfyUIClient = Depends(get_comfy_client),
    queue_mgr: QueueManager = Depends(get_queue_manager),
) -> Dict[str, Any]:
    comfy_stats: Dict[str, Any] = {}
    comfy_online = False
    try:
        comfy_stats = await comfy_client.get_system_stats()
        comfy_online = True
    except Exception as e:
        comfy_stats = {"error": str(e)}

    return {
        "hardware": {
            "gpu_name": settings.gpu_device_name,
            "gpu_vram_gb": settings.gpu_vram_gb,
            "gpu_backend": settings.gpu_backend,
            "max_concurrent_gpu_jobs": settings.max_concurrent_gpu_jobs,
        },
        "engine": {
            "comfyui_host": settings.comfyui_host,
            "comfyui_port": settings.comfyui_port,
            "comfyui_online": comfy_online,
            "comfyui_details": comfy_stats,
        },
        "queue": queue_mgr.get_stats(),
    }


@router.get("/queue", summary="Current queue status and lock state")
async def get_queue_info(queue_mgr: QueueManager = Depends(get_queue_manager)) -> Dict[str, Any]:
    return queue_mgr.get_stats()


@router.post("/gpu/free", summary="Trigger VRAM garbage collection and unload models")
async def free_gpu_memory(comfy_client: ComfyUIClient = Depends(get_comfy_client)) -> Dict[str, Any]:
    success = await comfy_client.free_memory(unload_models=True, free_memory=True)
    return {
        "status": "success" if success else "failed",
        "message": "GPU memory release command sent to ComfyUI"
    }
