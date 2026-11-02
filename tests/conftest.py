"""Tests never write mock jobs into the real production journal."""
import pytest
import app.core.queue_manager as queue_module
from app.core.config import get_settings

@pytest.fixture(autouse=True)
def separate_job_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(),"storage_base_dir",tmp_path)
    monkeypatch.setattr(queue_module,"_queue_manager_instance",None)
