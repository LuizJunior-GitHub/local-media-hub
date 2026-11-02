"""Persistent Flow-compatible quiz slots. Reference requirements are never discarded."""
import hashlib
import json
import re
import asyncio
import time
from pathlib import Path
from PIL import Image, ImageOps
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.core.queue_manager import get_queue_manager
from app.engine.template_engine import get_template_engine
from app.schemas.generation import FluxSchnellGenerateRequest, SDXLGenerateRequest, JobStatus, ModelEngine

router = APIRouter(prefix="/api/v1/quiz", tags=["Quiz production"])
SUFFIXES = {"P_main":"P", "R_main":"R", "P_bg":"P_bg", "R_bg":"R_bg"}
PROJECT_LOCKS = {}

def project_path(project_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", project_id):
        raise ValueError("project_id must contain 1-80 letters, digits, underscores or hyphens")
    root = get_settings().storage_base_dir / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{project_id}.json"

def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(4):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt==3:raise
            time.sleep(.05*(attempt+1))

def normalize(manifest):
    slots, refs = {}, set()
    if manifest.get("version") == 1 and isinstance(manifest.get("scenes"), list):
        project = manifest.get("project", {})
        title = project.get("title")
        for ref in manifest.get("characters", []) + manifest.get("assets", []):
            tag = str(ref.get("tag", "")).strip("[] ")
            if not tag or tag.casefold() in refs:
                raise ValueError("Missing or duplicate reference tag")
            refs.add(tag.casefold())
        for scene in manifest["scenes"]:
            index = scene.get("sourceIndex", "")
            match = re.fullmatch(r"(\d+)(P_bg|R_bg|P|R)", index)
            if not match:
                raise ValueError(f"Invalid sourceIndex: {index}")
            index = f"{int(match[1]):03d}{match[2]}"
            tags = scene.get("tags", [])
            if not isinstance(tags,list) or any(not isinstance(tag,str) or tag.strip("[] ").casefold() not in refs for tag in tags):
                raise ValueError(f"Unknown reference in {index}")
            if index in slots:
                raise ValueError(f"Duplicate sourceIndex: {index}")
            slots[index] = {"prompt":scene.get("prompt"),"reference_ids":tags,"enabled":True}
    elif manifest.get("schemaVersion") == 1:
        project = manifest.get("video", {})
        title = project.get("title")
        for group in ("characters","places","objects"):
            for ref in manifest.get("references",{}).get(group,[]):
                rid = ref.get("id")
                if not rid or rid in refs:
                    raise ValueError("Missing or duplicate reference id")
                refs.add(rid)
        orders = set()
        for scene in manifest.get("timeline", []):
            if scene.get("order") in orders:
                raise ValueError("Duplicate scene order")
            orders.add(scene.get("order"))
            if not str(scene.get("id", "")).isdigit():
                raise ValueError("Scene id must be numeric for quiz filenames")
            for role,suffix in SUFFIXES.items():
                slot = scene.get("images",{}).get(role)
                if not isinstance(slot,dict) or not isinstance(slot.get("enabled"),bool):
                    raise ValueError(f"Invalid slot {role}")
                links = slot.get("referenceIds")
                if not isinstance(links,list) or any(rid not in refs for rid in links):
                    raise ValueError("Unknown reference id")
                index = f"{int(scene['id']):03d}{suffix}"
                if index in slots:
                    raise ValueError("Duplicate scene id")
                slots[index] = {"prompt":slot.get("prompt"),"reference_ids":links,"enabled":slot["enabled"]}
    else:
        raise ValueError("Expected Flow version 1 or schemaVersion 1")
    if not isinstance(title,str) or not title.strip() or not slots:
        raise ValueError("A title and nonempty scenes are required")
    kits = {re.match(r"\d+", index)[0] for index in slots}
    for kit in kits:
        if any(kit+suffix not in slots for suffix in SUFFIXES.values()):
            raise ValueError(f"Kit {kit} requires P, R, P_bg and R_bg")
    for index,slot in slots.items():
        if not isinstance(slot["prompt"],str) or (slot["enabled"] and not slot["prompt"].strip()):
            raise ValueError(f"Invalid prompt in {index}")
        slot["filename"] = index + ".png"
    return {"title":title,"visual_style":project.get("visualStyle", ""),
            "extra_instructions":project.get("extraInstructions", ""),"slots":slots}

class ImportRequest(BaseModel):
    manifest: dict
    project_id: str

@router.post("/import")
async def import_manifest(req: ImportRequest):
    try:
        path = project_path(req.project_id)
        data = normalize(req.manifest)
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old["manifest"] != req.manifest:
                raise ValueError("Project id already exists with another manifest; use a new id")
            return old
        data.update(project_id=req.project_id,manifest=req.manifest)
        save(path,data)
        from app.services.quiz_production import initialize
        data=initialize(data)
        return data
    except ValueError as exc:
        raise HTTPException(422,str(exc))

class SlotRequest(BaseModel):
    project_id: str
    source_index: str
    seed: int = Field(ge=0,le=2**32-1)
    engine: str = "flux"
    retry_failed: bool = False
    attempt_id: str = Field(default="initial",pattern=r"^[A-Za-z0-9_-]{1,80}$")
    regenerate: bool = False

@router.post("/generate")
async def generate_slot(req: SlotRequest):
    from app.services.quiz_production import generate_slot as generate
    try:return await generate(req.project_id,req.source_index,req.seed,req.engine,req.attempt_id,req.retry_failed,req.regenerate)
    except (ValueError,RuntimeError) as exc:raise HTTPException(422,str(exc))

async def _generate_slot_locked(req: SlotRequest):
    try:
        path = project_path(req.project_id)
        if not path.exists():
            raise ValueError("Project not imported")
        data = json.loads(path.read_text(encoding="utf-8"))
        slot = data["slots"].get(req.source_index)
        if not slot or not slot["enabled"]:
            raise ValueError("Slot missing or disabled")
        if slot["reference_ids"]:
            raise ValueError("Reference-conditioned quiz production is not installed; refusing text-only substitution")
        queue = get_queue_manager()
        if slot.get("job_id"):
            existing = queue.get_job(slot["job_id"])
            if existing:
                if existing.status not in (JobStatus.FAILED,JobStatus.CANCELLED) or not req.retry_failed:
                    return existing
                if req.attempt_id == slot.get("attempt_id","initial"):
                    return existing
        elif req.retry_failed:
            raise ValueError("No failed job to retry")
        if req.engine not in ("flux","sdxl"):
            raise ValueError("engine must be flux or sdxl")
        prompt = "\n".join(p for p in [slot["prompt"],data["visual_style"],data["extra_instructions"]] if p)
        cls = FluxSchnellGenerateRequest if req.engine == "flux" else SDXLGenerateRequest
        validated = cls(prompt=prompt,seed=req.seed,width=1024,height=576,wait_for_completion=False)
        templates = get_template_engine()
        workflow,seed = (templates.build_flux_workflow if req.engine=="flux" else templates.build_sdxl_workflow)(
            prompt=validated.prompt,seed=req.seed,width=1024,height=576)
        job_id = await queue.submit_job(engine=ModelEngine.FLUX_SCHNELL if req.engine=="flux" else ModelEngine.SDXL,
            workflow=workflow,seed=seed,prompt=prompt,metadata={"project_id":req.project_id,"source_index":req.source_index},
            request_id=f"quiz:{req.project_id}:{req.source_index}:{req.attempt_id}")
        if slot.get("job_id"):
            slot.setdefault("previous_job_ids",[]).append(slot["job_id"])
        slot["job_id"] = job_id
        slot["attempt_id"] = req.attempt_id
        save(path,data)
        return queue.get_job(job_id)
    except (ValueError,RuntimeError) as exc:
        raise HTTPException(422,str(exc))

@router.post("/{project_id}/export")
async def export_project(project_id: str):
    try:
        path = project_path(project_id)
        if not path.exists():
            raise ValueError("Project not imported")
        data = json.loads(path.read_text(encoding="utf-8"))
        folder = path.parent / project_id
        folder.mkdir(exist_ok=True)
        result = {"project_id":project_id,"directory":str(folder.resolve()),"files":[],"missing":[],
                  "native_size":"1024x576","export_size":"1920x1080","resized":True}
        for index,slot in data["slots"].items():
            if not slot["enabled"]:
                continue
            job = get_queue_manager().get_job(slot.get("job_id", ""))
            if not job or job.status != JobStatus.COMPLETED or not job.images:
                result["missing"].append(index)
                continue
            target = folder / slot["filename"]
            temp = target.with_suffix(".tmp")
            try:
                with Image.open(job.images[0].file_path) as img:
                    ImageOps.fit(img.convert("RGB"),(1920,1080),method=Image.Resampling.LANCZOS).save(temp,format="PNG")
            except OSError as exc:
                result["missing"].append(index)
                result.setdefault("errors",[]).append({"source_index":index,"error":str(exc)})
                continue
            temp.replace(target)
            result["files"].append({"source_index":index,"filename":target.name,"file_path":str(target.resolve()),
                                    "job_id":job.job_id,"seed":job.images[0].seed,"engine":job.engine.value})
        save(folder / "production-index.json",result)
        return result
    except ValueError as exc:
        raise HTTPException(422,str(exc))
