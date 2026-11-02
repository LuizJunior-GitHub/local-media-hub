"""Explicit artistic character identity, shared by UI and MCP; no historical claims."""
FIELDS={
    'context':'Contexto artístico e região de referência',
    'apparent_age':'Idade aparente',
    'skin':'Pele',
    'face':'Formato do rosto e traços',
    'eyes':'Olhos',
    'hair':'Cabelo',
    'facial_hair':'Barba / pelos faciais',
    'build':'Constituição',
    'clothing':'Roupa, materiais e corte',
    'distinctive_features':'Características que devem se repetir',
    'avoid':'Elementos a evitar',
}
SPANISH={'context':'Dirección artística','apparent_age':'Edad aparente','skin':'Piel',
    'face':'Rostro','eyes':'Ojos','hair':'Cabello','facial_hair':'Vello facial',
    'build':'Constitución','clothing':'Vestuario','distinctive_features':'Rasgos de identidad','avoid':'Evitar'}
REQUIRED=['apparent_age','skin','face','eyes','hair','facial_hair','clothing']

def validate(profile):
    if not isinstance(profile,dict) or any(k not in FIELDS for k in profile):
        raise ValueError('Ficha visual inválida ou com campo desconhecido')
    if any(not isinstance(v,str) or len(v)>2000 for v in profile.values()):
        raise ValueError('Cada campo da ficha deve ser um texto de até 2000 caracteres')
    return {k:v.strip() for k,v in profile.items() if v.strip()}

def portrait_prompt(ref):
    profile=ref.get('visual_profile',{})
    if not profile:return ref['prompt']+'\nIdentity portrait, clearly visible face, chest up, neutral solid background. No text or logo.'
    # The source prompt is retained separately; its scene action cannot override this portrait.
    details='\n'.join(f'{SPANISH[k]}: {profile[k]}.' for k in FIELDS if profile.get(k))
    return (f"Retrato de referencia visual de {ref['name']}. Interpretación artística para el proyecto; "
        "la fisionomía no se presenta como una reconstrucción histórica documentada.\n"+details+
        '\nUna sola persona, encuadre desde el pecho hasta la cabeza completa, rostro claramente visible, '
        'mirada al frente, expresión natural, luz frontal neutra y suave, fondo liso gris sin edificios, '
        'sin acción narrativa ni herramientas, manos fuera del encuadre. '
        'Fotografía cinematográfica realista, textura natural de piel y tejidos, sin retoque de belleza. '
        'NO TEXT, NO LETTERS, NO LOGO, NO WATERMARK.')

def guidance(ref):
    profile=ref.get('visual_profile',{})
    if not profile:return ''
    # Clothing can legitimately vary by scene/phase; identity descriptors stay fixed.
    return 'Identidad visual artística ['+ref['tag']+']: '+ '; '.join(
        f'{SPANISH[k]}: {profile[k]}' for k in ['apparent_age','skin','face','eyes','hair','facial_hair','build','distinctive_features'] if profile.get(k))

def missing(ref):return [FIELDS[k] for k in REQUIRED if not ref.get('visual_profile',{}).get(k)]
