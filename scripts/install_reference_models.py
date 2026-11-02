"""Download official reference-capable weights, verify LFS SHA256, resume safely."""
import hashlib,json,time,urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MODELS=Path(r'C:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models')
SPECS=[('black-forest-labs/FLUX.2-klein-4b-fp8','flux-2-klein-4b-fp8.safetensors','diffusion_models'),
       ('Comfy-Org/z_image_turbo','split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors','text_encoders'),
       ('Comfy-Org/flux2-klein-4b','split_files/vae/flux2-vae.safetensors','vae')]
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def download(spec):
    repo,name,folder=spec
    metadata=json.load(urllib.request.urlopen('https://huggingface.co/api/models/'+repo+'?blobs=true',timeout=30))
    item=next(x for x in metadata['siblings'] if x['rfilename']==name)
    target=MODELS/folder/Path(name).name
    target.parent.mkdir(parents=True,exist_ok=True)
    digest=item['lfs']['sha256'];size=item['size']
    record={'repo':repo,'revision':metadata['sha'],'file':name,'size':size,'sha256':digest,'local_path':str(target)}
    if target.exists() and target.stat().st_size==size and sha(target)==digest:
        print('Already verified: '+target.name,flush=True);return record
    partial=target.with_suffix('.download')
    offset=partial.stat().st_size if partial.exists() else 0
    url=f'https://huggingface.co/{repo}/resolve/{metadata["sha"]}/{name}'
    headers={'Range':f'bytes={offset}-'} if offset else {}
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=90) as response:
        if offset and response.status!=206:offset=0
        mode='ab' if offset else 'wb';last=time.monotonic()
        with partial.open(mode) as f:
            while True:
                block=response.read(8*1024*1024)
                if not block:break
                f.write(block);offset+=len(block)
                if time.monotonic()-last>25:
                    print(f'{target.name}: {offset/size:.0%} ({offset/1e9:.2f}/{size/1e9:.2f} GB)',flush=True);last=time.monotonic()
    if partial.stat().st_size!=size or sha(partial)!=digest:
        raise RuntimeError('Download validation failed: '+str(partial))
    partial.replace(target)
    print('SHA256 verified: '+target.name,flush=True)
    return record
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(download,SPECS))
    (ROOT/'reference-models.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
