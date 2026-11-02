"""Real MCP stdio end-to-end test: four Flow slots on the local GPU."""
import asyncio,json,sys,time
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
def result_json(result):
    if result.is_error:
        raise RuntimeError(str(result.content))
    if result.structured_content:
        return result.structured_content
    return json.loads(next(c.text for c in result.content if c.type=='text'))

async def run():
    folder=ROOT/'work_codex'
    folder.mkdir(exist_ok=True)
    report={"transport":"MCP stdio","jobs":[],"started_at":time.time()}
    manifest={"version":1,"project":{"title":"Teste MCP — El arca de Noé",
      "visualStyle":"Cinematic realistic biblical illustration, warm natural lighting, ancient world, no text, no logos",
      "extraInstructions":"Landscape 16:9, historically inspired scene, no modern objects"},"characters":[],"assets":[],
      "scenes":[
        {"sourceIndex":"001P","sourceText":"¿Qué gran embarcación se construyó antes del diluvio?","tags":[],
         "prompt":"A large ancient timber ship under construction on dry land, massive curved wooden ribs and weathered planks, detailed realistic wood, ropes and simple ancient tools in foreground, dramatic sky, wide cinematic view, no people"},
        {"sourceIndex":"001R","sourceText":"El arca de Noé","tags":[],
         "prompt":"The completed enormous ancient wooden ark floating on calm floodwaters, two pairs of birds flying in the distance, golden sunrise through mist, mountain peaks beyond, wide cinematic composition, detailed timber construction, no people"},
        {"sourceIndex":"001P_bg","sourceText":"Fondo pregunta","tags":[],
         "prompt":"Empty ancient woodland clearing at dawn, rough timber beams stacked along the outer edges, warm earth and distant mountains, center kept uncluttered for a quiz overlay, soft cinematic light, no people, no ship"},
        {"sourceIndex":"001R_bg","sourceText":"Fondo respuesta","tags":[],
         "prompt":"Quiet ancient mountain landscape after a great rain, receding water in a valley, gentle golden sunlight and small distant rainbow, clear uncluttered center for quiz overlay, realistic cinematic illustration, no people, no ship"}]}
    save=lambda: (folder/'mcp-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'mcp_bridge.py')],cwd=str(ROOT))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=30) as session:
            init=await session.initialize()
            report['server']=init.server_info.model_dump(mode='json')
            report['tools']=[t.name for t in (await session.list_tools()).tools]
            print('MCP tools discovered: '+', '.join(report['tools']),flush=True)
            report['capabilities']=result_json(await session.call_tool('get_capabilities',{}))
            result_json(await session.call_tool('import_quiz_manifest',{'manifest':manifest,'project_id':'teste-mcp-arca-20261011'}))
            for index,slot in enumerate(manifest['scenes']):
                args={'project_id':'teste-mcp-arca-20261011','source_index':slot['sourceIndex'],'seed':7112026+index}
                start=time.monotonic()
                job=result_json(await session.call_tool('generate_quiz_slot',args))
                duplicate=result_json(await session.call_tool('generate_quiz_slot',args))
                assert duplicate['job_id']==job['job_id'], 'Duplicate submission generated a second job'
                print(f"{slot['sourceIndex']}: {job['job_id']} {job['status']}",flush=True)
                while job['status'] not in ('completed','failed','cancelled','uncertain'):
                    if time.monotonic()-start>900:
                        report['monitoring_timeout']=job
                        save()
                        raise TimeoutError('Monitoring stopped; original job remains recoverable. Do not resubmit.')
                    await asyncio.sleep(5)
                    job=result_json(await session.call_tool('get_job',{'job_id':job['job_id']}))
                    print(f"{slot['sourceIndex']}: {job['status']} ({time.monotonic()-start:.0f}s)",flush=True)
                report['jobs'].append(job)
                save()
                if job['status']!='completed':
                    raise RuntimeError(job.get('error','Generation failed'))
            report['export']=result_json(await session.call_tool('export_quiz_project',{'project_id':'teste-mcp-arca-20261011'}))
            assert len(report['export']['files'])==4 and not report['export']['missing']
            generic=result_json(await session.call_tool('generate_image',{
                'prompt':'Cinematic realistic illustration of an ancient biblical wooden ark, an enormous enclosed rectangular timber cargo vessel with a flat bottom, three levels, a long pitched roof and a single small upper window, floating peacefully on floodwaters among distant mountains at golden dawn. Simple massive wooden box-shaped construction. No sails, no masts, no flags, no modern ship details, no people, no text, no logo.',
                'request_id':'mcp-generic-ark-final-20261011','seed':8311,'engine':'flux','width':1024,'height':576,'steps':4}))
            monitor_started=time.monotonic()
            while generic['status'] not in ('completed','failed','cancelled','uncertain'):
                if time.monotonic()-monitor_started>900:
                    report['generic_monitoring_timeout']=generic
                    save()
                    raise TimeoutError('Original generic job remains recoverable; do not resubmit')
                await asyncio.sleep(3)
                generic=result_json(await session.call_tool('get_job',{'job_id':generic['job_id']}))
                print('Generic MCP image: '+generic['status']+' steps '+str(generic['current_step'])+'/'+str(generic['total_steps']),flush=True)
            assert generic['status']=='completed'
            assert generic['total_steps']==4, 'Real progress stream did not report sampling steps'
            report['generic_smoke']=generic
            report['passed']=True
            save()
            print(json.dumps(report['export'],ensure_ascii=True),flush=True)

if __name__=='__main__':
    asyncio.run(run())
