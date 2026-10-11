import json
import re
from io import BytesIO
from pathlib import Path
from typing import List, Optional
from PIL import Image

from app.core.config import get_settings
from app.schemas.assets import CharacterAnchor, LocationAnchor, AssetListResponse, utc_now


class AssetService:
    """
    Manages physical storage, image validation, and metadata registry
    for character and location anchor references.
    """

    def __init__(self):
        self.settings = get_settings()

    @staticmethod
    def sanitize_slug(slug: str) -> str:
        """Sanitize an ID into a clean filesystem-friendly slug."""
        clean = re.sub(r"[^\w\-_]", "_", slug.strip().lower())
        return re.sub(r"_+", "_", clean)

    def _get_image_dimensions(self, image_bytes: bytes) -> tuple[int, int]:
        """Inspect image binary to extract width and height."""
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                return img.width, img.height
        except Exception:
            return 1024, 1024

    # --------------------------------------------------------------------------
    # Character Anchors
    # --------------------------------------------------------------------------

    def list_characters(self) -> List[CharacterAnchor]:
        """List all registered character anchors from storage."""
        char_dir = self.settings.storage_characters_dir
        characters: List[CharacterAnchor] = []

        if not char_dir.exists():
            return characters

        for meta_file in sorted(char_dir.glob("*.json")):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                img_path = char_dir / data.get("image_filename", "")
                if img_path.exists():
                    characters.append(CharacterAnchor(**data))
            except Exception:
                continue

        return characters

    def get_character(self, character_id: str) -> Optional[CharacterAnchor]:
        """Get a character anchor by ID."""
        slug = self.sanitize_slug(character_id)
        meta_file = self.settings.storage_characters_dir / f"{slug}.json"
        if not meta_file.exists():
            return None

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CharacterAnchor(**data)
        except Exception:
            return None

    def save_character(
        self,
        character_id: str,
        name: str,
        file_bytes: bytes,
        original_filename: str,
        description: Optional[str] = None,
        default_prompt_modifier: Optional[str] = None,
    ) -> CharacterAnchor:
        """Save a new or updated character anchor image and sidecar metadata."""
        slug = self.sanitize_slug(character_id)
        ext = Path(original_filename).suffix.lower()
        if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
            ext = ".png"

        image_filename = f"{slug}{ext}"
        image_path = self.settings.storage_characters_dir / image_filename
        meta_path = self.settings.storage_characters_dir / f"{slug}.json"

        # Write image binary
        with open(image_path, "wb") as f:
            f.write(file_bytes)

        width, height = self._get_image_dimensions(file_bytes)
        now = utc_now()

        existing = self.get_character(slug)
        created_at = existing.created_at if existing else now

        anchor = CharacterAnchor(
            id=slug,
            name=name,
            description=description,
            image_filename=image_filename,
            file_path=str(image_path.resolve()),
            url=f"/references/characters/{image_filename}",
            default_prompt_modifier=default_prompt_modifier,
            width=width,
            height=height,
            created_at=created_at,
            updated_at=now,
        )

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(anchor.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

        return anchor

    def delete_character(self, character_id: str) -> bool:
        """Delete character image and metadata."""
        slug = self.sanitize_slug(character_id)
        meta_file = self.settings.storage_characters_dir / f"{slug}.json"
        if not meta_file.exists():
            return False

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            img_file = self.settings.storage_characters_dir / data.get("image_filename", "")
            if img_file.exists():
                img_file.unlink()
            meta_file.unlink()
            return True
        except Exception:
            return False

    # --------------------------------------------------------------------------
    # Location Anchors
    # --------------------------------------------------------------------------

    def list_locations(self) -> List[LocationAnchor]:
        """List all registered location anchors from storage."""
        loc_dir = self.settings.storage_locations_dir
        locations: List[LocationAnchor] = []

        if not loc_dir.exists():
            return locations

        for meta_file in sorted(loc_dir.glob("*.json")):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                img_path = loc_dir / data.get("image_filename", "")
                if img_path.exists():
                    locations.append(LocationAnchor(**data))
            except Exception:
                continue

        return locations

    def get_location(self, location_id: str) -> Optional[LocationAnchor]:
        """Get a location anchor by ID."""
        slug = self.sanitize_slug(location_id)
        meta_file = self.settings.storage_locations_dir / f"{slug}.json"
        if not meta_file.exists():
            return None

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return LocationAnchor(**data)
        except Exception:
            return None

    def save_location(
        self,
        location_id: str,
        name: str,
        file_bytes: bytes,
        original_filename: str,
        description: Optional[str] = None,
        style_notes: Optional[str] = None,
    ) -> LocationAnchor:
        """Save a new or updated location anchor image and sidecar metadata."""
        slug = self.sanitize_slug(location_id)
        ext = Path(original_filename).suffix.lower()
        if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
            ext = ".png"

        image_filename = f"{slug}{ext}"
        image_path = self.settings.storage_locations_dir / image_filename
        meta_path = self.settings.storage_locations_dir / f"{slug}.json"

        # Write image binary
        with open(image_path, "wb") as f:
            f.write(file_bytes)

        width, height = self._get_image_dimensions(file_bytes)
        now = utc_now()

        existing = self.get_location(slug)
        created_at = existing.created_at if existing else now

        anchor = LocationAnchor(
            id=slug,
            name=name,
            description=description,
            image_filename=image_filename,
            file_path=str(image_path.resolve()),
            url=f"/references/locations/{image_filename}",
            style_notes=style_notes,
            width=width,
            height=height,
            created_at=created_at,
            updated_at=now,
        )

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(anchor.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

        return anchor

    def delete_location(self, location_id: str) -> bool:
        """Delete location image and metadata."""
        slug = self.sanitize_slug(location_id)
        meta_file = self.settings.storage_locations_dir / f"{slug}.json"
        if not meta_file.exists():
            return False

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            img_file = self.settings.storage_locations_dir / data.get("image_filename", "")
            if img_file.exists():
                img_file.unlink()
            meta_file.unlink()
            return True
        except Exception:
            return False

    def list_all_assets(self) -> AssetListResponse:
        """List both characters and locations."""
        characters = self.list_characters()
        locations = self.list_locations()
        return AssetListResponse(
            characters=characters,
            locations=locations,
            total_characters=len(characters),
            total_locations=len(locations),
        )


_asset_service_instance: Optional[AssetService] = None


def get_asset_service() -> AssetService:
    global _asset_service_instance
    if _asset_service_instance is None:
        _asset_service_instance = AssetService()
    return _asset_service_instance
