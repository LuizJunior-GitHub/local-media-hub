import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.api import routes_assets, routes_generate, routes_system
from app.core.config import get_settings
from app.core.queue_manager import get_queue_manager
from app.engine.comfy_client import get_comfy_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("local-media-hub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes storage, starts the GPU queue manager worker,
    and conducts health probes.
    """
    settings = get_settings()
    settings.ensure_directories_exist()

    queue_mgr = get_queue_manager()
    await queue_mgr.start()

    comfy_client = get_comfy_client()
    is_healthy = await comfy_client.is_healthy()
    if is_healthy:
        logger.info(f"Connected to headless ComfyUI backend at {settings.comfy_http_url}")
    else:
        logger.warning(
            f"ComfyUI is currently unreachable at {settings.comfy_http_url}. "
            "Local Media Hub will start, but GPU generation requests will queue until ComfyUI is online."
        )

    yield

    logger.info("Shutting down Local Media Hub...")
    await queue_mgr.stop()
    await comfy_client.close()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="Local Media Hub",
        description=(
            "High-performance unified Gateway and MCP Server for local GPU image generation "
            "(AMD Radeon RX 7800 XT DirectML/ROCm + Headless ComfyUI backend)."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static storage for image distribution
    app.mount("/outputs", StaticFiles(directory=str(settings.storage_outputs_dir)), name="outputs")
    app.mount("/references", StaticFiles(directory=str(settings.storage_base_dir / "references")), name="references")

    # Serve interactive web studio frontend
    from pathlib import Path
    from fastapi.responses import FileResponse
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_frontend():
            return FileResponse(str(static_dir / "index.html"))

    # Register API routers
    app.include_router(routes_generate.router)
    app.include_router(routes_assets.router)
    app.include_router(routes_system.router)

    return app


app = create_app()


def run_server():
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Local Media Hub Server")
    parser.add_argument(
        "--mcp-stdio",
        action="store_true",
        help="Run as Model Context Protocol (MCP) server over STDIO instead of HTTP API"
    )
    args = parser.parse_args()

    if args.mcp_stdio:
        from app.mcp.server import run_mcp_stdio_server
        logger.info("Starting Local Media Hub in MCP STDIO Mode...")
        asyncio.run(run_mcp_stdio_server())
    else:
        settings = get_settings()
        uvicorn.run(
            "main:app",
            host=settings.host,
            port=settings.port,
            reload=settings.debug,
            log_level="info" if not settings.debug else "debug",
        )


if __name__ == "__main__":
    run_server()
