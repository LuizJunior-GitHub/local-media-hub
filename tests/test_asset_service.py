import pytest
from io import BytesIO
from PIL import Image
from app.services.asset_service import AssetService, get_asset_service


def test_list_existing_assets():
    service = get_asset_service()
    chars = service.list_characters()
    assert any(c.id == "moises" for c in chars)

    locs = service.list_locations()
    assert any(l.id == "templo_salomao" for l in locs)


def test_save_and_delete_character():
    service = get_asset_service()

    # Generate test image in memory
    img = Image.new("RGB", (512, 512), color="blue")
    buf = BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    # Save
    anchor = service.save_character(
        character_id="test_davi",
        name="Rei Davi",
        file_bytes=img_bytes,
        original_filename="davi.png",
        description="Jovem pastor com funda e harpa",
        default_prompt_modifier="young king david, biblical robes, cinematic",
    )

    assert anchor.id == "test_davi"
    assert anchor.name == "Rei Davi"
    assert anchor.width == 512
    assert anchor.height == 512

    # Verify retrieval
    retrieved = service.get_character("test_davi")
    assert retrieved is not None
    assert retrieved.name == "Rei Davi"

    # Delete
    deleted = service.delete_character("test_davi")
    assert deleted is True
    assert service.get_character("test_davi") is None


def test_save_and_delete_location():
    service = get_asset_service()

    img = Image.new("RGB", (640, 640), color="green")
    buf = BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    anchor = service.save_location(
        location_id="test_sinai",
        name="Monte Sinai",
        file_bytes=img_bytes,
        original_filename="sinai.png",
        description="Montanha rochosa com nuvens de tempestade",
        style_notes="dramatic rugged cliffs, stormy skies",
    )

    assert anchor.id == "test_sinai"
    assert anchor.width == 640

    retrieved = service.get_location("test_sinai")
    assert retrieved is not None
    assert retrieved.name == "Monte Sinai"

    deleted = service.delete_location("test_sinai")
    assert deleted is True
    assert service.get_location("test_sinai") is None
