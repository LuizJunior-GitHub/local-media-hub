"""Real MCP proof: import Exodo, generate six references, reuse them in three scenes."""
import asyncio,json,sys,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
MANIFEST=Path(r'C:\Users\JUNINHO\Documents\ChatGPT\QUIZ ES\outputs\producao_cronometrada_20261011\quiz_exodo_30_flow.json')
PROJECT='exodo-local-referencias-20261011'
def value(r):
    if r.is_error:raise RuntimeError(str(r.content))
    return r.structured_content or json.loads(next(c.text for c in r.content if c.type=='text'))
async def run():
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    report={'project_id':PROJECT,'manifest':str(MANIFEST),'transport':'MCP stdio'}
    save=lambda:(ROOT/'work_codex'/'reference-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'mcp_bridge.py')],cwd=str(ROOT))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=60) as client:
            async def call(name,args):return value(await client.call_tool(name,args))
            await client.initialize()
            report['capabilities']=await call('get_capabilities',{})
            if not report['capabilities']['reference_composition']:raise RuntimeError('Reference model not ready')
            await call('import_quiz_manifest',{'manifest':manifest,'project_id':PROJECT})
            await call('configure_quiz_project',{'project_id':PROJECT,'changes':{'engine':'klein','manual_review':False}})
            await call('start_quiz_batch',{'project_id':PROJECT,'phase':'references'})
            begin=time.monotonic()
            while True:
                project=await call('get_quiz_project',{'project_id':PROJECT})
                names={r['name']:{'shots':len(r['shots']),'approved':bool(r['approved_shot_id']),
                     'jobs':[x['status'] for x in r['candidate_status'] if x]} for r in project['reference_catalog'].values()}
                print(json.dumps({'seconds':round(time.monotonic()-begin),'references':names,'batch':project['batch']},ensure_ascii=True),flush=True)
                report['project']=project;save()
                if project['batch']['status']!='running':break
                if time.monotonic()-begin>1800:raise TimeoutError('Monitoring stopped; batch remains saved, do not duplicate requests')
                await asyncio.sleep(8)
            assert all(r['approved_shot_id'] for r in project['reference_catalog'].values()),project['batch']['message']
            source_indexes=['003P','003R']
            multi=max([(i,s) for i,s in project['slots'].items() if s['enabled']],key=lambda x:len(x[1]['reference_ids']))[0]
            if multi not in source_indexes:source_indexes.append(multi)
            report['tested_slots']=source_indexes
            await call('start_quiz_batch',{'project_id':PROJECT,'phase':'scenes','source_indexes':source_indexes})
            begin=time.monotonic()
            while True:
                project=await call('get_quiz_project',{'project_id':PROJECT})
                print(json.dumps({'seconds':round(time.monotonic()-begin),'scenes':{i:{'status':project['slots'][i]['status'],
                    'references':project['slots'][i]['reference_ids'],'job':project['slots'][i]['job']} for i in source_indexes},
                    'batch':project['batch']},ensure_ascii=True),flush=True)
                report['project']=project;save()
                if project['batch']['status']!='running':break
                if time.monotonic()-begin>1800:raise TimeoutError('Monitoring stopped; batch remains saved')
                await asyncio.sleep(8)
            assert all(project['slots'][i]['status']=='completed' for i in source_indexes),project['batch']['message']
            jobs=[]
            for index in source_indexes:
                slot=project['slots'][index]
                job=await call('get_job',{'job_id':slot['job_id']})
                assert len(job['metadata']['reference_image_filenames'])==len(set(slot['reference_ids']))
                assert len([n for n in job['metadata']['workflow'].values() if n['class_type']=='LoadImage'])==len(set(slot['reference_ids']))
                jobs.append(job)
            report['jobs']=jobs;report['export']=await call('export_quiz_project',{'project_id':PROJECT})
            report['passed']=True;save();print('REAL REFERENCE PIPELINE PASSED',flush=True)
if __name__=='__main__':asyncio.run(run())
