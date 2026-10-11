import pytest
from pathlib import Path
from app.core.config import Settings, get_settings


def test_settings_defaults():
    settings = get_settings()
    assert settings.app_name == "Local Media Hub"
    assert settings.gpu_vram_gb == 16
    assert settings.max_concurrent_gpu_jobs == 1
    assert "127.0.0.1" in settings.comfyui_host
    assert settings.comfyui_port == 8188
    assert settings.comfy_http_url == "http://127.0.0.1:8188"
    assert "ws://127.0.0.1:8188/ws" in settings.comfy_ws_url


def test_directories_creation():
    settings = get_settings()
    settings.ensure_directories_exist()
    assert settings.storage_outputs_dir.exists()
    assert settings.storage_characters_dir.exists()
    assert settings.storage_locations_dir.exists()
