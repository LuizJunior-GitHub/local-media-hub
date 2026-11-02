"""Qwen Edit 2511 GGUF graph, retaining every supplied real reference.

Uses Comfy-Org's Plus encoder and index_timestep_zero reference mode.
Native encoder supports three images: excess links are rejected, never dropped.
"""
def build_qwen_edit_workflow(settings,prompt,seed,width=1024,height=576,reference_images=None,steps=40):
    if width%16 or height%16 or not (256<=width<=2048 and 256<=height<=2048):
        raise ValueError('Qwen dimensions must be multiples of 16 between 256 and 2048')
    if type(steps) is not int or not 10<=steps<=60:raise ValueError('Qwen steps must be between 10 and 60')
    refs=list(reference_images or [])
    if len(refs)>3:raise ValueError('Qwen Edit Plus supports at most three reference images; none may be discarded')
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    graph={
      '1':node('UnetLoaderGGUF',unet_name=settings.default_qwen_edit_model),
      '2':node('CLIPLoader',clip_name=settings.default_qwen_edit_clip,type='qwen_image',device='default'),
      '3':node('VAELoader',vae_name=settings.default_qwen_edit_vae),
      '4':node('TextEncodeQwenImageEditPlus',clip=['2',0],prompt=prompt),
      '5':node('TextEncodeQwenImageEditPlus',clip=['2',0],prompt=''),
      '6':node('EmptySD3LatentImage',width=width,height=height,batch_size=1),
      '7':node('ModelSamplingAuraFlow',model=['1',0],shift=3.1),
      '8':node('CFGNorm',model=['7',0],strength=1.0,pre_cfg=False)}
    positive,negative=['4',0],['5',0]
    tiled={'tile_size':512,'overlap':64,'temporal_size':64,'temporal_overlap':8}
    for index,filename in enumerate(refs):
        load,scale,encode,pos,neg=map(str,range(20+index*5,25+index*5))
        graph[load]=node('LoadImage',image=filename)
        graph[scale]=node('ImageScaleToTotalPixels',image=[load,0],upscale_method='lanczos',megapixels=.25,resolution_steps=16)
        graph['4']['inputs'][f'image{index+1}']=[scale,0]
        graph['5']['inputs'][f'image{index+1}']=[scale,0]
        # Encode low-resolution references explicitly, avoiding implicit 1MP
        # upscaling of every reference inside the Plus text encoder.
        graph[encode]=node('VAEEncodeTiled',pixels=[scale,0],vae=['3',0],**tiled)
        graph[pos]=node('ReferenceLatent',conditioning=positive,latent=[encode,0])
        graph[neg]=node('ReferenceLatent',conditioning=negative,latent=[encode,0])
        positive,negative=[pos,0],[neg,0]
    graph['9']=node('FluxKontextMultiReferenceLatentMethod',conditioning=positive,reference_latents_method='index_timestep_zero')
    graph['10']=node('FluxKontextMultiReferenceLatentMethod',conditioning=negative,reference_latents_method='index_timestep_zero')
    graph['11']=node('KSampler',model=['8',0],positive=['9',0],negative=['10',0],latent_image=['6',0],
                     seed=int(seed),steps=steps,cfg=4.0,sampler_name='euler',scheduler='simple',denoise=1.0)
    graph['12']=node('VAEDecodeTiled',samples=['11',0],vae=['3',0],**tiled)
    graph['13']=node('SaveImage',images=['12',0],filename_prefix='LMH_QWEN_EDIT_2511')
    return graph,int(seed)
