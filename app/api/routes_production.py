import json,zipfile
from io import BytesIO
from pathlib import Path
from fastapi import APIRouter,HTTPException,UploadFile,File,Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from app.services import quiz_production as production

router=APIRouter(prefix='/api/v1/production',tags=['Quiz Studio'])
class RefRequest(BaseModel):
    reference_id:str
    seed:int=Field(ge=0,le=2**32-1)
    attempt_id:str=Field(default='initial',pattern=r'^[A-Za-z0-9_-]{1,80}$')
class ApprovalRequest(BaseModel):
    reference_id:str
    shot_id:str
class EditRequest(BaseModel):
    changes:dict
class BatchRequest(BaseModel):
    phase:str='all'
    source_indexes:list[str]|None=None

async def action(fn,*args,**kwargs):
    try:return await fn(*args,**kwargs)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc))

@router.get('/projects')
async def list_projects():
    from app.core.config import get_settings
    folder=get_settings().storage_base_dir/'projects';items=[]
    for path in folder.glob('*.json'):
        try:
            d=json.loads(path.read_text(encoding='utf-8'))
            items.append({'project_id':d['project_id'],'title':d['title']})
        except (ValueError,KeyError):continue
    return {'projects':items}

@router.get('/{project_id}')
async def get_project(project_id:str):return await action(production.get_project,project_id)
@router.patch('/{project_id}/settings')
async def configure(project_id:str,req:EditRequest):return await action(production.configure,project_id,req.changes)
@router.post('/{project_id}/restore')
async def restore(project_id:str,req:EditRequest):return await action(production.restore_snapshot,project_id,req.changes)
@router.patch('/{project_id}/{target}/{item_id}')
async def edit(project_id:str,target:str,item_id:str,req:EditRequest):
    if target not in ['slot','reference']:raise HTTPException(422,'Alvo inválido')
    return await action(production.edit_item,project_id,target,item_id,req.changes)
@router.post('/{project_id}/references/generate')
async def generate_reference(project_id:str,req:RefRequest):
    return await action(production.generate_reference,project_id,req.reference_id,req.seed,req.attempt_id)
@router.post('/{project_id}/references/approve')
async def approve(project_id:str,req:ApprovalRequest):return await action(production.approve_reference,project_id,req.reference_id,req.shot_id)
@router.post('/{project_id}/references/upload')
async def upload(project_id:str,reference_id:str=Form(...),approve_image:bool=Form(False),file:UploadFile=File(...)):
    content=await file.read(25*1024*1024+1)
    return await action(production.upload_reference,project_id,reference_id,content,approve_image)
@router.post('/{project_id}/batch/start')
async def start(project_id:str,req:BatchRequest):return await action(production.start_batch,project_id,req.phase,req.source_indexes)
@router.post('/{project_id}/batch/pause')
async def pause(project_id:str):return await action(production.pause_batch,project_id)
@router.get('/{project_id}/zip')
async def download_zip(project_id:str):
    from app.api.routes_quiz import export_project
    exported=await export_project(project_id);buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as zipfile_out:
        for item in exported['files']:zipfile_out.write(item['file_path'],item['filename'])
        zipfile_out.writestr('production-index.json',json.dumps(exported,ensure_ascii=False,indent=2))
    buffer.seek(0)
    return StreamingResponse(buffer,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{project_id}_Kits.zip"'})
