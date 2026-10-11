from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, field_validator


class ModelEngine(str, Enum):
    SDXL = "sdxl"
    FLUX_SCHNELL = "flux_schnell"
    CHARACTER_CONSISTENCY = "character_consistency"
    LOCATION_CONSISTENCY = "location_consistency"


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BaseGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000, description="Text prompt for image generation")
    seed: Optional[int] = Field(default=None, ge=0, le=2**32 - 1, description="Random seed for reproducibility")
    width: int = Field(default=1024, ge=512, le=2048, description="Image width (must be multiple of 8)")
    height: int = Field(default=1024, ge=512, le=2048, description="Image height (must be multiple of 8)")
    wait_for_completion: bool = Field(
        default=True,
        description="If True, endpoint blocks until generation completes and returns image info. If False, returns 202 with job_id immediately."
    )

    @field_validator("width", "height")
    @classmethod
    def validate_multiples_of_8(cls, v: int) -> int:
        if v % 8 != 0:
            raise ValueError(f"Dimension {v} must be a multiple of 8")
        return v


class SDXLGenerateRequest(BaseGenerateRequest):
    negative_prompt: str = Field(
        default="ugly, deformed, disfigured, poor details, bad anatomy, blur, watermark, text, lowres",
        description="Negative prompt for filtering unwanted artifacts"
    )
    steps: int = Field(default=30, ge=10, le=60, description="Inference sampling steps (typically 25-35 for SDXL)")
    cfg_scale: float = Field(default=7.0, ge=1.0, le=20.0, description="Classifier-Free Guidance scale")
    sampler_name: str = Field(default="dpmpp_2m", description="Sampler algorithm name in ComfyUI")
    scheduler: str = Field(default="karras", description="Scheduler type (karras, normal, sgm_uniform, etc.)")
    checkpoint: Optional[str] = Field(default=None, description="Optional custom checkpoint name to override default")


class FluxSchnellGenerateRequest(BaseGenerateRequest):
    steps: int = Field(default=4, ge=1, le=12, description="Inference steps for Flux.1 Schnell (optimally 4 steps)")
    guidance: float = Field(default=3.5, ge=1.0, le=10.0, description="Flux CFG / Guidance scale")
    sampler_name: str = Field(default="euler", description="Sampler algorithm name (euler recommended for Flux)")
    scheduler: str = Field(default="simple", description="Scheduler name (simple recommended for Flux)")


class CharacterGenerateRequest(BaseGenerateRequest):
    character_id: str = Field(..., description="ID of the character anchor registered in storage/references/characters")
    negative_prompt: str = Field(
        default="ugly, deformed face, bad eyes, disfigured, bad anatomy, duplicate, blur",
        description="Negative prompt"
    )
    face_strength: float = Field(default=0.85, ge=0.1, le=1.0, description="IP-Adapter / FaceID weight intensity")
    steps: int = Field(default=30, ge=15, le=50, description="Sampling steps")
    cfg_scale: float = Field(default=7.0, ge=1.0, le=15.0, description="CFG scale")
    sampler_name: str = Field(default="dpmpp_2m", description="Sampler algorithm")
    scheduler: str = Field(default="karras", description="Scheduler type")


class LocationGenerateRequest(BaseGenerateRequest):
    location_id: str = Field(..., description="ID of the location anchor registered in storage/references/locations")
    negative_prompt: str = Field(
        default="modern buildings, cars, artifacts, out of place objects, low quality, blur",
        description="Negative prompt"
    )
    style_strength: float = Field(default=0.75, ge=0.1, le=1.0, description="IP-Adapter style transfer strength")
    depth_strength: float = Field(default=0.60, ge=0.0, le=1.0, description="ControlNet Depth structural adherence")
    steps: int = Field(default=30, ge=15, le=50, description="Sampling steps")
    cfg_scale: float = Field(default=7.0, ge=1.0, le=15.0, description="CFG scale")
    sampler_name: str = Field(default="dpmpp_2m", description="Sampler algorithm")
    scheduler: str = Field(default="karras", description="Scheduler type")


class GeneratedImageInfo(BaseModel):
    filename: str
    subfolder: str = ""
    url: str
    file_path: str
    content_type: str = "image/png"
    size_bytes: int
    width: int
    height: int
    seed: int


class GenerationJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    engine: ModelEngine
    prompt: str
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: float = 0.0  # 0.0 to 1.0
    current_step: int = 0
    total_steps: int = 0
    prompt_id: Optional[str] = None
    images: List[GeneratedImageInfo] = []
    error: Optional[str] = None
    execution_time_seconds: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class JobListResponse(BaseModel):
    jobs: List[GenerationJobResponse]
    total: int
