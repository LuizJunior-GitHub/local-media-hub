from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """
    Centralized configuration management for Local Media Hub.
    Loads settings from .env file or environment variables with strict typing.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Application & API Gateway
    app_name: str = "Local Media Hub"
    app_env: Literal["development", "production", "test"] = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: Union[List[str], str] = ["*"]

    # ComfyUI Headless Engine Connection
    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 8188
    comfyui_ssl: bool = False
    comfyui_client_id: str = "local-media-hub-worker"
    comfyui_timeout_seconds: int = 360

    # Hardware & GPU Resource Management
    # Target: AMD Radeon RX 7800 XT (16 GB GDDR6) via DirectML / ROCm
    gpu_device_name: str = "AMD Radeon RX 7800 XT"
    gpu_vram_gb: int = 16
    gpu_backend: Literal["directml", "rocm", "cuda", "cpu"] = "directml"
    max_concurrent_gpu_jobs: int = 1  # Strict lock for single-GPU 16GB VRAM safety
    demo_fallback: bool = True  # Allows UI and pipeline testing when ComfyUI backend is offline

    # Default Models
    default_sdxl_model: str = "juggernautXL_v9Rundiffusion.safetensors"
    default_flux_model: str = "flux1-schnell-Q4_K_S.gguf"
    default_clip_l: str = "clip_l.safetensors"
    default_t5_xxl: str = "t5xxl_fp8_e4m3fn.safetensors"
    default_flux_vae: str = "ae.safetensors"
    default_ipadapter_face_model: str = "ip-adapter-faceid-plusv2_sdxl.bin"
    default_controlnet_depth_model: str = "controlnet-depth-sdxl-1.0.safetensors"

    # Storage Paths
    storage_base_dir: Path = Field(default=PROJECT_ROOT / "storage")
    storage_outputs_dir: Path = Field(default=PROJECT_ROOT / "storage" / "outputs")
    storage_characters_dir: Path = Field(default=PROJECT_ROOT / "storage" / "references" / "characters")
    storage_locations_dir: Path = Field(default=PROJECT_ROOT / "storage" / "references" / "locations")

    # MCP Settings
    mcp_server_name: str = "local-media-hub"
    mcp_transport: Literal["stdio", "sse"] = "stdio"
    mcp_sse_host: str = "0.0.0.0"
    mcp_sse_port: int = 8001

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[List[str], str]) -> List[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator(
        "storage_base_dir",
        "storage_outputs_dir",
        "storage_characters_dir",
        "storage_locations_dir",
        mode="before"
    )
    @classmethod
    def resolve_paths(cls, v: Union[str, Path]) -> Path:
        path = Path(v)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def comfy_http_url(self) -> str:
        protocol = "https" if self.comfyui_ssl else "http"
        return f"{protocol}://{self.comfyui_host}:{self.comfyui_port}"

    @property
    def comfy_ws_url(self) -> str:
        protocol = "wss" if self.comfyui_ssl else "ws"
        return f"{protocol}://{self.comfyui_host}:{self.comfyui_port}/ws?clientId={self.comfyui_client_id}"

    def ensure_directories_exist(self) -> None:
        """Create storage directories if they do not exist."""
        self.storage_outputs_dir.mkdir(parents=True, exist_ok=True)
        self.storage_characters_dir.mkdir(parents=True, exist_ok=True)
        self.storage_locations_dir.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories_exist()
    return settings
