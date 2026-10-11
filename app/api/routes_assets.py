from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.schemas.assets import AssetListResponse, CharacterAnchor, LocationAnchor
from app.services.asset_service import AssetService, get_asset_service

router = APIRouter(prefix="/api/v1/assets", tags=["Anchor Assets"])


# ------------------------------------------------------------------------------
# Overview
# ------------------------------------------------------------------------------
@router.get("", response_model=AssetListResponse, summary="List all character and location anchors")
async def list_all_assets(asset_service: AssetService = Depends(get_asset_service)):
    return asset_service.list_all_assets()


# ------------------------------------------------------------------------------
# Character Anchors
# ------------------------------------------------------------------------------
@router.get("/characters", response_model=List[CharacterAnchor], summary="List registered character anchors")
async def list_characters(asset_service: AssetService = Depends(get_asset_service)):
    return asset_service.list_characters()


@router.get("/characters/{character_id}", response_model=CharacterAnchor, summary="Get character anchor by ID")
async def get_character(character_id: str, asset_service: AssetService = Depends(get_asset_service)):
    char = asset_service.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail=f"Character anchor '{character_id}' not found")
    return char


@router.post(
    "/characters",
    response_model=CharacterAnchor,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and register a new character anchor image"
)
async def upload_character(
    id: str = Form(..., description="Unique slug ID (e.g., 'moises', 'davi')"),
    name: str = Form(..., description="Display name"),
    description: Optional[str] = Form(None, description="Visual description"),
    default_prompt_modifier: Optional[str] = Form(None, description="Keywords to append automatically"),
    file: UploadFile = File(..., description="Anchor image file (PNG, JPG, WEBP)"),
    asset_service: AssetService = Depends(get_asset_service),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    anchor = asset_service.save_character(
        character_id=id,
        name=name,
        file_bytes=content,
        original_filename=file.filename,
        description=description,
        default_prompt_modifier=default_prompt_modifier,
    )
    return anchor


@router.delete("/characters/{character_id}", summary="Delete character anchor")
async def delete_character(character_id: str, asset_service: AssetService = Depends(get_asset_service)):
    success = asset_service.delete_character(character_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Character anchor '{character_id}' not found or could not be deleted")
    return {"status": "deleted", "character_id": character_id}


# ------------------------------------------------------------------------------
# Location Anchors
# ------------------------------------------------------------------------------
@router.get("/locations", response_model=List[LocationAnchor], summary="List registered location anchors")
async def list_locations(asset_service: AssetService = Depends(get_asset_service)):
    return asset_service.list_locations()


@router.get("/locations/{location_id}", response_model=LocationAnchor, summary="Get location anchor by ID")
async def get_location(location_id: str, asset_service: AssetService = Depends(get_asset_service)):
    loc = asset_service.get_location(location_id)
    if not loc:
        raise HTTPException(status_code=404, detail=f"Location anchor '{location_id}' not found")
    return loc


@router.post(
    "/locations",
    response_model=LocationAnchor,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and register a new location anchor image"
)
async def upload_location(
    id: str = Form(..., description="Unique slug ID (e.g., 'monte_sinai', 'templo_salomao')"),
    name: str = Form(..., description="Display name"),
    description: Optional[str] = Form(None, description="Visual description"),
    style_notes: Optional[str] = Form(None, description="Style or architectural atmosphere notes"),
    file: UploadFile = File(..., description="Anchor image file (PNG, JPG, WEBP)"),
    asset_service: AssetService = Depends(get_asset_service),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    anchor = asset_service.save_location(
        location_id=id,
        name=name,
        file_bytes=content,
        original_filename=file.filename,
        description=description,
        style_notes=style_notes,
    )
    return anchor


@router.delete("/locations/{location_id}", summary="Delete location anchor")
async def delete_location(location_id: str, asset_service: AssetService = Depends(get_asset_service)):
    success = asset_service.delete_location(location_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Location anchor '{location_id}' not found or could not be deleted")
    return {"status": "deleted", "location_id": location_id}
