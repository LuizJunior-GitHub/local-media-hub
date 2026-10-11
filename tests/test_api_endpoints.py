import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_frontend_root_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "Local Media Hub" in response.text
        assert "text/html" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["service"] == "Local Media Hub"
        assert "queue" in data
        assert data["gpu_target"] == "AMD Radeon RX 7800 XT"


@pytest.mark.asyncio
async def test_status_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/status")
        assert response.status_code == 200
        data = response.json()
        assert "hardware" in data
        assert data["hardware"]["gpu_vram_gb"] == 16
        assert "queue" in data


@pytest.mark.asyncio
async def test_assets_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # All assets
        res_all = await client.get("/api/v1/assets")
        assert res_all.status_code == 200
        data_all = res_all.json()
        assert "characters" in data_all
        assert "locations" in data_all

        # Characters
        res_char = await client.get("/api/v1/assets/characters")
        assert res_char.status_code == 200
        chars = res_char.json()
        assert any(c["id"] == "moises" for c in chars)

        # Single character
        res_moises = await client.get("/api/v1/assets/characters/moises")
        assert res_moises.status_code == 200
        assert res_moises.json()["name"] == "Moisés"

        # Locations
        res_loc = await client.get("/api/v1/assets/locations")
        assert res_loc.status_code == 200
        locs = res_loc.json()
        assert any(l["id"] == "templo_salomao" for l in locs)


@pytest.mark.asyncio
async def test_async_job_submission():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Submit async job (wait_for_completion=False)
        payload = {
            "prompt": "Test prophet in the desert",
            "seed": 42,
            "width": 1024,
            "height": 1024,
            "steps": 25,
            "wait_for_completion": False,
        }
        res = await client.post("/api/v1/generate/sdxl", json=payload)
        assert res.status_code == 202
        job_data = res.json()
        job_id = job_data["job_id"]
        assert job_id is not None
        assert job_data["status"] == "queued"

        # Query job status
        res_job = await client.get(f"/api/v1/jobs/{job_id}")
        assert res_job.status_code == 200
        assert res_job.json()["job_id"] == job_id

        # List jobs
        res_list = await client.get("/api/v1/jobs")
        assert res_list.status_code == 200
        assert any(j["job_id"] == job_id for j in res_list.json()["jobs"])
