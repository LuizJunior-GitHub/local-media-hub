"""Flow-style production state shared by web UI and MCP, with approved image references."""
import asyncio,base64,hashlib,json,re,uuid
from io import BytesIO
from pathlib import Path
from PIL import Image
from fastapi import HTTPException
from app.core.config import get_settings
from app.core.queue_manager import get_queue_manager
from app.engine.comfy_client import get_comfy_client
from app.engine.template_engine import get_template_engine
from app.schemas.generation import JobStatus,ModelEngine
from app.services import character_profiles

BATCH_TASKS={}
TERMINAL={JobStatus.COMPLETED,JobStatus.FAILED,JobStatus.CANCELLED,JobStatus.UNCERTAIN}
KINDS={'characters':'character','places':'place','objects':'object'}
GUIDANCE={'character':'Preserve facial structure, eye color, hair, beard, age and distinctive features. Adapt pose and framing. Do not copy the portrait background.',
          'place':'Preserve architecture, materials and color palette. Adapt camera, action and weather.',
          'object':'Preserve shape, material and distinctive details. Adapt perspective and scale.'}

def storage(project_id):
    from app.api.routes_quiz import project_path
    return project_path(project_id)
def lock(project_id):
    from app.api.routes_quiz import PROJECT_LOCKS
    return PROJECT_LOCKS.setdefault(project_id,asyncio.Lock())
def write(data):
    from app.api.routes_quiz import save
    save(storage(data['project_id']),data)
def image_info(path):
    p=Path(path).resolve()
    root=get_settings().storage_base_dir.resolve()
    if not p.is_relative_to(root) or not p.is_file():raise ValueError('Local reference image is unavailable')
    with Image.open(p) as im:im.verify()
    return p
def image_url(path):
    p=Path(path).resolve()
    for folder,url in [(get_settings().storage_outputs_dir,'/outputs'),(get_settings().storage_base_dir/'projects','/project-media')]:
        root=folder.resolve()
        if p.is_relative_to(root):return url+'/'+p.relative_to(root).as_posix()
    return None

def initialize(data):
    if 'reference_catalog' in data:return data
    raw=data['manifest'];catalog={}
    if raw.get('schemaVersion')==1:
        definitions=[{**r,'kind':KINDS[k]} for k in KINDS for r in raw.get('references',{}).get(k,[])]
    else:
        definitions=[{**r,'id':r['tag'],'kind':'character'} for r in raw.get('characters',[])]+[
            {**r,'id':r['tag'],'kind':r['type']} for r in raw.get('assets',[])]
    for r in definitions:
        if r.get('kind') not in GUIDANCE or not all(isinstance(r.get(k),str) and r[k].strip() for k in ['id','name','tag']):
            raise ValueError('Invalid reference definition')
        prompt=r.get('referencePrompt',r.get('prompt',''))
        if not isinstance(prompt,str) or not prompt.strip():raise ValueError('Reference prompt is required')
        catalog[r['id']]={'id':r['id'],'tag':r['tag'],'name':r['name'],'kind':r['kind'],
                         'prompt':prompt,'shots':[],'approved_shot_id':None,'version':0,'candidate_jobs':[]}
        if r['kind']=='character':
            catalog[r['id']]['visual_profile']=character_profiles.validate(r.get('visualProfile',r.get('visual_profile',{})))
        for shot in r.get('shots',[]):
            if shot.get('base64'):
                imported=store_reference_image(data['project_id'],base64.b64decode(shot['base64'],validate=True))
                imported['source_id']=shot.get('id');catalog[r['id']]['shots'].append(imported)
                if shot.get('id')==r.get('approvedShotId'):
                    catalog[r['id']]['approved_shot_id']=imported['id'];catalog[r['id']]['version']=1
    data['reference_catalog']=catalog
    general={suffix:any(s['enabled'] for index,s in data['slots'].items() if index.endswith(suffix)
                 and (suffix.endswith('_bg') or not index.endswith('_bg'))) for suffix in ['P','R','P_bg','R_bg']}
    for index,s in data['slots'].items():
        role=re.sub(r'^\d+','',index)
        s['selection_override']=None if s['enabled']==general[role] else s['enabled']
        s['reviewed']=False;s['reference_snapshot']=[]
        if raw.get('version')==1:
            s['reference_ids']=[next(rid for rid,r in catalog.items() if r['tag'].strip('[] ').casefold()==tag.strip('[] ').casefold()) for tag in s['reference_ids']]
    data['settings']={'global_selection':general,'manual_review':False,'use_same_background':bool(raw.get('video',{}).get('useSameBackground')),'engine':'klein'}
    data['batch']={'status':'idle','message':'Projeto importado. Gere ou reutilize as referências.','operation_id':None}
    write(data);return data

def load(project_id):
    path=storage(project_id)
    if not path.exists():raise HTTPException(404,'Projeto não encontrado')
    return initialize(json.loads(path.read_text(encoding='utf-8')))

def store_reference_image(project_id,content):
    if len(content)>25*1024*1024:raise ValueError('Reference image exceeds 25 MB')
    with Image.open(BytesIO(content)) as im:
        if im.width*im.height>20_000_000:raise ValueError('Reference image too large')
        converted=im.convert('RGB')
        converted.thumbnail((1536,1536),Image.Resampling.LANCZOS)
        shot_id=str(uuid.uuid4());folder=storage(project_id).parent/project_id/'references'
        folder.mkdir(parents=True,exist_ok=True);path=folder/(shot_id+'.png')
        converted.save(path,format='PNG')
        return {'id':shot_id,'file_path':str(path.resolve()),'url':image_url(path),'width':converted.width,'height':converted.height,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source':'imported'}

def approved(ref):
    return next((s for s in ref['shots'] if s['id']==ref.get('approved_shot_id')),None)
def snapshots(data,slot):
    result=[]
    for rid in dict.fromkeys(slot['reference_ids']):
        ref=data['reference_catalog'].get(rid)
        shot=approved(ref) if ref else None
        if not shot:raise ValueError('Aprove uma imagem para '+(ref['name'] if ref else rid))
        path=image_info(shot['file_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=shot['sha256']:
            raise ValueError('Reference image changed on disk; import and approve again')
        result.append({'reference_id':rid,'shot_id':shot['id'],'version':ref['version'],
                       'file_path':str(path),'sha256':shot['sha256'],'tag':ref['tag'],'kind':ref['kind']})
    return result

def approve_in(data,rid,shot_id):
    ref=data['reference_catalog'].get(rid)
    shot=next((s for s in ref['shots'] if s['id']==shot_id),None) if ref else None
    if not shot:raise ValueError('Imagem de referência não encontrada')
    image_info(shot['file_path'])
    if ref['approved_shot_id']!=shot_id:
        ref['approved_shot_id']=shot_id;ref['version']+=1

def synchronize(data):
    queue=get_queue_manager();changed=False
    for ref in data['reference_catalog'].values():
        for candidate in ref['candidate_jobs']:
            job=queue.get_job(candidate['job_id'])
            if job and job.status==JobStatus.COMPLETED and job.images and not candidate.get('shot_id'):
                img=job.images[0];path=image_info(img.file_path)
                shot={'id':job.job_id,'job_id':job.job_id,'file_path':str(path),'url':image_url(path),
                      'width':img.width,'height':img.height,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source':'generated'}
                ref['shots'].append(shot);candidate['shot_id']=shot['id'];changed=True
                if not data['settings']['manual_review'] and not ref['approved_shot_id']:
                    approve_in(data,ref['id'],shot['id'])
    if data['batch']['status']=='running' and data['project_id'] not in BATCH_TASKS:
        data['batch'].update(status='paused',message='Servidor reiniciado. Confira jobs incertos antes de retomar.');changed=True
    if changed:write(data)
    return data

def job_view(job):
    if not job:return None
    return {'job_id':job.job_id,'status':job.status.value,'error':job.error,'progress':job.progress,
            'seconds':job.execution_time_seconds,'images':[{'file_path':i.file_path,'url':image_url(i.file_path),'width':i.width,'height':i.height} for i in job.images]}

def view(data):
    synchronize(data);queue=get_queue_manager()
    for ref in data['reference_catalog'].values():
        ref['candidate_status']=[job_view(queue.get_job(c['job_id'])) for c in ref['candidate_jobs']]
        if ref['kind']=='character':
            ref['visual_profile']=ref.get('visual_profile',{})
            ref['profile_missing']=character_profiles.missing(ref)
            ref['effective_reference_prompt']=character_profiles.portrait_prompt(ref)
            ref['profile_fields']=character_profiles.FIELDS
    for index,slot in data['slots'].items():
        job=queue.get_job(slot.get('job_id',''));slot['job']=job_view(job)
        slot['status']=job.status.value if job else 'pending';slot['blocked_reason']='';slot['stale']=False
        if not slot['enabled']:slot['status']='disabled'
        else:
            try:
                current=snapshots(data,slot)
                if job and job.status==JobStatus.COMPLETED:
                    slot['stale']=([s['shot_id'] for s in current]!=[s['shot_id'] for s in slot['reference_snapshot']] or
                        slot.get('prompt_hash',hashlib.sha256(slot['prompt'].encode()).hexdigest())!=hashlib.sha256(slot['prompt'].encode()).hexdigest())
                if not job and data['settings']['manual_review'] and not slot['reviewed']:
                    slot['status']='blocked';slot['blocked_reason']='Cena aguarda revisão'
            except ValueError as exc:
                if not job:slot['status']='blocked'
                slot['blocked_reason']=str(exc)
                if job and job.status==JobStatus.COMPLETED:slot['stale']=True
    return data

async def get_project(project_id):
    async with lock(project_id):return view(load(project_id))

async def generate_reference(project_id,rid,seed,attempt_id='initial',batch_owner=None):
    async with lock(project_id):
        data=load(project_id);synchronize(data);ref=data['reference_catalog'].get(rid)
        if batch_owner and (data['batch']['operation_id']!=batch_owner or data['batch']['status']!='running'):
            raise ValueError('Fila pausada antes de iniciar nova referência')
        if not ref:raise ValueError('Referência não encontrada')
        for c in ref['candidate_jobs']:
            job=get_queue_manager().get_job(c['job_id'])
            if c['attempt_id']==attempt_id or (job and job.status not in TERMINAL):return job
        if ref['kind']=='character' and data['settings'].get('require_character_profiles',False):
            missing=character_profiles.missing(ref)
            if missing:raise ValueError('Complete a ficha visual de '+ref['name']+': '+', '.join(missing))
        capabilities=await get_comfy_client().capabilities()
        if not capabilities['reference_composition']:raise ValueError('Modelos de referências ainda indisponíveis: '+', '.join(capabilities['klein_missing']))
        suffix={'character':'Identity portrait, clearly visible face, chest up, neutral solid background. No text or logo.',
                'place':'Wide architecture and environment reference, no people, no text or logo.',
                'object':'Isolated object reference, neutral background, no people, no text or logo.'}[ref['kind']]
        prompt=character_profiles.portrait_prompt(ref) if ref['kind']=='character' else ref['prompt']+'\n'+suffix
        wf,seed=get_template_engine().build_klein_workflow(prompt,seed,width=768,height=768)
        job_id=await get_queue_manager().submit_job(ModelEngine.FLUX_KLEIN,wf,seed,prompt,
            metadata={'project_id':project_id,'reference_id':rid,'kind':'reference','steps':4,'visual_profile':ref.get('visual_profile',{})},request_id=f'ref:{project_id}:{rid}:{attempt_id}')
        ref['candidate_jobs'].append({'job_id':job_id,'attempt_id':attempt_id,'prompt':prompt});write(data)
        return get_queue_manager().get_job(job_id)

async def approve_reference(project_id,rid,shot_id):
    async with lock(project_id):
        data=load(project_id);synchronize(data);approve_in(data,rid,shot_id);write(data);return view(data)

async def upload_reference(project_id,rid,content,approve_image=False):
    async with lock(project_id):
        data=load(project_id);ref=data['reference_catalog'].get(rid)
        if not ref:raise ValueError('Referência não encontrada')
        shot=store_reference_image(project_id,content);ref['shots'].append(shot)
        if approve_image:approve_in(data,rid,shot['id'])
        write(data);return shot

async def generate_slot(project_id,index,seed,engine='klein',attempt_id='initial',retry_failed=False,regenerate=False,batch_owner=None):
    async with lock(project_id):
        data=load(project_id);synchronize(data);slot=data['slots'].get(index);queue=get_queue_manager()
        if batch_owner and (data['batch']['operation_id']!=batch_owner or data['batch']['status']!='running'):
            raise ValueError('Fila pausada antes de iniciar nova cena')
        if not slot or not slot['enabled']:raise ValueError('Slot ausente ou desativado')
        old=queue.get_job(slot.get('job_id',''))
        if old:
            if old.status not in TERMINAL or old.status==JobStatus.UNCERTAIN:return old
            if attempt_id==slot.get('attempt_id','initial'):return old
            if old.status==JobStatus.COMPLETED and not regenerate:return old
            if old.status in (JobStatus.FAILED,JobStatus.CANCELLED) and not retry_failed:return old
        refs=snapshots(data,slot)
        if data['settings']['manual_review'] and not slot['reviewed']:raise ValueError('Revise a cena antes de gerar')
        if refs and engine not in ['klein','qwen']:raise ValueError('Use Klein ou Qwen para aplicar as imagens aprovadas, sem descartar referências')
        if engine not in ['klein','qwen','flux','sdxl']:raise ValueError('Modelo inválido')
        if engine=='qwen' and len(refs)>3:raise ValueError('Qwen aceita no máximo três imagens de referência; nenhum vínculo será descartado')
        if data['settings']['use_same_background'] and index.endswith('R_bg') and not refs:
            prior=data['slots'].get(index[:-4]+'P_bg',{});job=queue.get_job(prior.get('job_id',''))
            if job and job.status==JobStatus.COMPLETED:
                slot.update(job_id=job.job_id,reused_from=index[:-4]+'P_bg',reference_snapshot=[],attempt_id=attempt_id);write(data);return job
        prompt='\n'.join(x for x in [slot['prompt'],data['visual_style'],data['extra_instructions']] if x)
        if refs:
            prompt+='\n\nCreate a NEW scene as described above using these reference images for identity only. Do not reproduce a reference sheet or the portrait background.\n'
            prompt+='\n'.join(f"Image {i+1} [{r['tag']}] ({r['kind']}): {GUIDANCE[r['kind']]}" for i,r in enumerate(refs))
            identities=[character_profiles.guidance(data['reference_catalog'][r['reference_id']]) for r in refs if r['kind']=='character']
            prompt+='\n'+'\n'.join(x for x in identities if x)
        templates=get_template_engine();uploaded=[]
        if engine=='qwen':
            from app.engine.qwen_graph import build_qwen_edit_workflow
            caps=await get_comfy_client().capabilities()
            if not caps.get('qwen_reference_composition'):raise ValueError('Modelos Qwen indisponíveis: '+', '.join(caps.get('qwen_missing',[])))
            for r in refs:uploaded.append(await get_comfy_client().upload_image(Path(r['file_path'])))
            wf,seed=build_qwen_edit_workflow(get_settings(),prompt,seed,reference_images=uploaded,steps=data['settings'].get('qwen_steps',40))
            model=ModelEngine.QWEN_IMAGE_EDIT
        elif engine=='klein':
            caps=await get_comfy_client().capabilities()
            if not caps['reference_composition']:raise ValueError('Modelos de composição indisponíveis')
            for r in refs:uploaded.append(await get_comfy_client().upload_image(Path(r['file_path'])))
            wf,seed=templates.build_klein_workflow(prompt,seed,reference_images=uploaded,steps=data['settings'].get('klein_steps',4))
            model=ModelEngine.FLUX_KLEIN
        else:
            wf,seed=(templates.build_flux_workflow if engine=='flux' else templates.build_sdxl_workflow)(prompt,seed,width=1024,height=576)
            model=ModelEngine.FLUX_SCHNELL if engine=='flux' else ModelEngine.SDXL
        if old:slot.setdefault('previous_job_ids',[]).append(old.job_id)
        job_id=await queue.submit_job(model,wf,seed,prompt,metadata={'project_id':project_id,'source_index':index,'kind':'scene',
            'reference_snapshot':refs,'reference_image_filenames':uploaded,'steps':data['settings'].get('qwen_steps',40) if engine=='qwen' else (data['settings'].get('klein_steps',4) if engine=='klein' else (4 if engine=='flux' else 30))},
            request_id=f'quiz:{project_id}:{index}:{attempt_id}')
        slot.update(job_id=job_id,attempt_id=attempt_id,reference_snapshot=refs,
                    prompt_hash=hashlib.sha256(slot['prompt'].encode()).hexdigest());write(data);return queue.get_job(job_id)

async def configure(project_id,changes):
    async with lock(project_id):
        data=load(project_id);settings=data['settings']
        if 'klein_steps' in changes:
            value=changes['klein_steps']
            if type(value) is not int or not 1<=value<=16:raise ValueError('Passos Klein: inteiro de 1 a 16; padrão recomendado 4')
            settings['klein_steps']=value
        if 'qwen_steps' in changes:
            value=changes['qwen_steps']
            if type(value) is not int or not 10<=value<=60:raise ValueError('Passos Qwen: inteiro de 10 a 60; padrão 40')
            settings['qwen_steps']=value
        if 'global_selection' in changes:
            for k,v in changes['global_selection'].items():
                if k not in settings['global_selection'] or not isinstance(v,bool):raise ValueError('Seleção inválida')
                settings['global_selection'][k]=v
        for k in ['manual_review','use_same_background','require_character_profiles']:
            if k in changes:
                if not isinstance(changes[k],bool):raise ValueError('Configuração inválida')
                settings[k]=changes[k]
        if changes.get('review_all'):
            for slot in data['slots'].values():slot['reviewed']=True
        if 'engine' in changes:
            if changes['engine'] not in ['klein','qwen','flux','sdxl']:raise ValueError('Modelo inválido')
            settings['engine']=changes['engine']
        for index,slot in data['slots'].items():
            override=slot.get('selection_override');role=re.sub(r'^\d+','',index)
            slot['enabled']=settings['global_selection'][role] if override is None else override
        write(data);return view(data)

async def edit_item(project_id,target,item_id,changes):
    async with lock(project_id):
        data=load(project_id)
        item=(data['slots'] if target=='slot' else data['reference_catalog']).get(item_id)
        if not item:raise ValueError('Item não encontrado')
        expected=changes.get('expected',{})
        if not isinstance(expected,dict):raise ValueError('Estado esperado inválido')
        for field,value in expected.items():
            if field not in ['prompt','reference_ids','reviewed','selection_override','visual_profile']:raise ValueError('Campo de comparação inválido')
            if item.get(field,{} if field=='visual_profile' else None)!=value:
                raise ValueError('Outra operação alterou este item enquanto você editava. Seu texto permanece no editor; copie-o e reabra para comparar antes de salvar.')
        if 'prompt' in changes:
            if not isinstance(changes['prompt'],str) or not changes['prompt'].strip():raise ValueError('Prompt vazio')
            item['prompt']=changes['prompt'];item['reviewed']=True
        if target=='reference':
            if 'visual_profile' in changes:
                if item['kind']!='character':raise ValueError('Ficha de fisionomia disponível apenas para personagens')
                profile=character_profiles.validate(changes['visual_profile'])
                if profile!=item.get('visual_profile',{}) and item.get('approved_shot_id'):
                    item['previous_approved_shot_id']=item['approved_shot_id']
                    item['approved_shot_id']=None;item['version']+=1
                item['visual_profile']=profile
            if changes.get('clear_approval'):
                item['approved_shot_id']=None;item['version']+=1
        if target=='slot':
            if 'reference_ids' in changes:
                links=changes['reference_ids']
                if not isinstance(links,list) or any(r not in data['reference_catalog'] for r in links):raise ValueError('Vínculo inválido')
                item['reference_ids']=list(dict.fromkeys(links))
            if 'reviewed' in changes:item['reviewed']=bool(changes['reviewed'])
            if 'selection_override' in changes:
                v=changes['selection_override']
                if v is not None and not isinstance(v,bool):raise ValueError('Seleção inválida')
                item['selection_override']=v;item['enabled']=data['settings']['global_selection'][re.sub(r'^\d+','',item_id)] if v is None else v
        write(data);return view(data)

async def restore_snapshot(project_id,snapshot):
    """Restore saved editing choices on this PC, never replace job or file identities."""
    async with lock(project_id):
        data=load(project_id)
        if data['batch']['status']=='running':raise ValueError('Pause a produção antes de restaurar o estado')
        if snapshot.get('project_id')!=project_id or set(snapshot.get('slots',{}))!=set(data['slots']):
            raise ValueError('O backup não corresponde a este projeto salvo neste PC')
        settings=snapshot.get('settings',{})
        if settings.get('engine') not in ['klein','flux','sdxl']:raise ValueError('Modelo inválido no backup')
        if any(not isinstance(settings.get(k),bool) for k in ['manual_review','use_same_background']):
            raise ValueError('Configuração inválida no backup')
        general=settings.get('global_selection',{})
        if set(general)!=set(data['settings']['global_selection']) or any(not isinstance(v,bool) for v in general.values()):
            raise ValueError('Seleção inválida no backup')
        for rid,ref in snapshot.get('reference_catalog',{}).items():
            if rid not in data['reference_catalog']:raise ValueError('Referência desconhecida no backup')
            if not isinstance(ref.get('prompt'),str) or not ref['prompt'].strip():raise ValueError('Prompt vazio')
            data['reference_catalog'][rid]['prompt']=ref['prompt']
            if 'visual_profile' in ref:data['reference_catalog'][rid]['visual_profile']=character_profiles.validate(ref['visual_profile'])
            if ref.get('approved_shot_id'):approve_in(data,rid,ref['approved_shot_id'])
        data['settings']=settings
        for index,saved in snapshot['slots'].items():
            links=saved.get('reference_ids',[]);override=saved.get('selection_override')
            if not isinstance(links,list) or any(r not in data['reference_catalog'] for r in links):raise ValueError('Vínculo inválido no backup')
            if override is not None and not isinstance(override,bool):raise ValueError('Seleção inválida')
            if not isinstance(saved.get('prompt'),str) or not saved['prompt'].strip():raise ValueError('Prompt vazio')
            slot=data['slots'][index]
            slot.update(prompt=saved['prompt'],reference_ids=list(dict.fromkeys(links)),selection_override=override,reviewed=bool(saved.get('reviewed')))
            slot['enabled']=general[re.sub(r'^\d+','',index)] if override is None else override
        write(data);return view(data)

async def start_batch(project_id,phase='all',source_indexes=None):
    if phase not in ['all','references','scenes']:raise ValueError('Etapa inválida')
    async with lock(project_id):
        data=load(project_id);synchronize(data)
        if project_id in BATCH_TASKS and data['batch']['status']=='running':return view(data)
        if source_indexes and any(x not in data['slots'] for x in source_indexes):raise ValueError('Slot inválido')
        owner=str(uuid.uuid4());data['batch']={'status':'running','operation_id':owner,'phase':phase,
            'source_indexes':source_indexes,'message':'Fila iniciada.','current_job_id':None};write(data)
        BATCH_TASKS[project_id]=asyncio.create_task(run_batch(project_id,owner))
        return view(data)

async def pause_batch(project_id):
    async with lock(project_id):
        data=load(project_id);data['batch'].update(status='paused',message='Pausa solicitada. O job atual pode terminar; nenhum próximo será iniciado.');write(data);return view(data)

async def run_batch(project_id,owner):
    try:
        while True:
            async with lock(project_id):
                data=load(project_id);synchronize(data)
                if data['batch']['operation_id']!=owner or data['batch']['status']!='running':return
                phase=data['batch']['phase'];target=None;queue=get_queue_manager()
                # Attach to existing work before considering new submissions.
                pending=[j for j in queue.list_jobs(10000) if j.metadata.get('project_id')==project_id and j.status not in TERMINAL]
                if pending:job=pending[-1]
                else:
                    if phase in ['all','references']:
                        for ref in data['reference_catalog'].values():
                            if not ref['shots'] and not ref['candidate_jobs']:target=('ref',ref['id']);break
                    if not target and phase in ['all','scenes']:
                        selected=data['batch'].get('source_indexes') or list(data['slots'])
                        for index in selected:
                            slot=data['slots'][index]
                            if slot['enabled'] and not slot.get('job_id'):
                                try:snapshots(data,slot)
                                except ValueError:continue
                                if not data['settings']['manual_review'] or slot['reviewed']:target=('slot',index);break
                    if not target:
                        final=view(data);selected=data['batch'].get('source_indexes') or list(data['slots'])
                        unresolved=[i for i in selected if final['slots'][i]['enabled'] and final['slots'][i]['status']!='completed']
                        unapproved=[r['name'] for r in data['reference_catalog'].values() if not r['approved_shot_id']]
                        complete=(not unresolved if phase!='references' else not unapproved)
                        data['batch'].update(status='finished' if complete else 'paused',message='Etapa concluída.' if complete else
                            f'Fila pausada: {len(unresolved)} cenas pendentes/bloqueadas; {len(unapproved)} referências sem aprovação.');write(data);return
            if not pending:
                seed=int(hashlib.sha256((project_id+':'+target[1]).encode()).hexdigest()[:8],16)
                job=await (generate_reference(project_id,target[1],seed,batch_owner=owner) if target[0]=='ref' else
                           generate_slot(project_id,target[1],seed,engine=data['settings']['engine'],batch_owner=owner))
            async with lock(project_id):
                data=load(project_id)
                if data['batch']['operation_id']==owner:
                    data['batch']['current_job_id']=job.job_id;write(data)
            while job.status not in TERMINAL:
                await asyncio.sleep(1);job=get_queue_manager().get_job(job.job_id)
            if job.status!=JobStatus.COMPLETED:
                async with lock(project_id):
                    data=load(project_id)
                    if data['batch']['operation_id']==owner:
                        data['batch'].update(status='paused',message='Falha: '+str(job.error)+'. Nova tentativa exige ação explícita.');write(data)
                return
    except asyncio.CancelledError:raise
    except Exception as exc:
        async with lock(project_id):
            data=load(project_id)
            if data['batch']['operation_id']==owner:data['batch'].update(status='paused',message=str(exc));write(data)
    finally:
        if BATCH_TASKS.get(project_id) is asyncio.current_task():BATCH_TASKS.pop(project_id,None)

async def stop_batches():
    tasks=list(BATCH_TASKS.values())
    for task in tasks:task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
