from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssetType(str, Enum):
    CHARACTER = "character"
    LOCATION = "location"


class CharacterAnchor(BaseModel):
    id: str = Field(..., description="Unique slug ID (e.g. 'moises', 'davi')")
    name: str = Field(..., description="Display name of the character")
    description: Optional[str] = Field(default=None, description="Visual description of the character")
    image_filename: str = Field(..., description="Anchor image filename in storage/references/characters")
    file_path: str = Field(..., description="Absolute path on disk")
    url: str = Field(..., description="Accessible HTTP URL for preview")
    default_prompt_modifier: Optional[str] = Field(
        default=None,
        description="Prompt additions automatically included when using this character"
    )
    width: int = Field(default=1024, description="Width of anchor image")
    height: int = Field(default=1024, description="Height of anchor image")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LocationAnchor(BaseModel):
    id: str = Field(..., description="Unique slug ID (e.g. 'monte_sinai', 'templo_salomao')")
    name: str = Field(..., description="Display name of the location")
    description: Optional[str] = Field(default=None, description="Visual description of the location")
    image_filename: str = Field(..., description="Anchor image filename in storage/references/locations")
    file_path: str = Field(..., description="Absolute path on disk")
    url: str = Field(..., description="Accessible HTTP URL for preview")
    style_notes: Optional[str] = Field(
        default=None,
        description="Notes on architecture, lighting, or atmosphere to maintain"
    )
    width: int = Field(default=1024, description="Width of anchor image")
    height: int = Field(default=1024, description="Height of anchor image")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AssetListResponse(BaseModel):
    characters: List[CharacterAnchor]
    locations: List[LocationAnchor]
    total_characters: int
    total_locations: int
