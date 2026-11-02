import pytest
from app.services import character_profiles as cp

def ref():
    return {'name':'Noé','tag':'Noe','prompt':'Noé construyendo un arca, fondo de madera',
        'visual_profile':{'apparent_age':'75 años','skin':'oliva cálida','face':'ancho, arrugas',
            'eyes':'marrones','hair':'gris ondulado largo','facial_hair':'barba larga gris',
            'clothing':'túnica rectangular sin botones'}}

def test_identity_portrait_does_not_copy_scene_action():
    prompt=cp.portrait_prompt(ref())
    assert '75 años' in prompt and 'túnica rectangular sin botones' in prompt
    assert 'construyendo' not in prompt and 'fondo de madera' not in prompt
    assert 'Interpretación artística' in prompt and 'cabeza completa' in prompt
    assert cp.missing(ref())==[]

def test_scene_identity_preserves_features_without_locking_clothes():
    prompt=cp.guidance(ref())
    assert 'barba larga gris' in prompt and 'túnica' not in prompt

def test_profile_validation_and_no_silent_facts():
    assert cp.validate({'skin':' oliva '})=={'skin':'oliva'}
    with pytest.raises(ValueError):cp.validate({'birthplace':'invented'})
    with pytest.raises(ValueError):cp.validate({'eyes':42})
    raw={'name':'Unknown','prompt':'Original detailed portrait'}
    assert cp.portrait_prompt(raw).startswith(raw['prompt'])
    assert cp.missing(raw)

@pytest.mark.asyncio
async def test_profile_edit_requires_new_approval_and_preserves_images(monkeypatch):
    from app.services import quiz_production as p
    data={'project_id':'test','reference_catalog':{'noe':{**ref(),'id':'noe','kind':'character',
        'version':1,'approved_shot_id':'old','shots':[{'id':'old'}]}}}
    monkeypatch.setattr(p,'load',lambda _:data)
    monkeypatch.setattr(p,'write',lambda _:None)
    monkeypatch.setattr(p,'view',lambda d:d)
    updated=await p.edit_item('test','reference','noe',{'visual_profile':{**ref()['visual_profile'],'hair':'blanco'}})
    char=updated['reference_catalog']['noe']
    assert char['approved_shot_id'] is None and char['shots']==[{'id':'old'}]
    assert char['previous_approved_shot_id']=='old' and char['version']==2

@pytest.mark.asyncio
async def test_required_profile_blocks_generic_portrait_before_gpu(monkeypatch):
    from app.services import quiz_production as p
    data={'project_id':'guard','reference_catalog':{'noe':{'id':'noe','name':'Noé','kind':'character','visual_profile':{},'candidate_jobs':[]}},
        'settings':{'require_character_profiles':True}}
    monkeypatch.setattr(p,'load',lambda _:data)
    monkeypatch.setattr(p,'synchronize',lambda d:d)
    with pytest.raises(ValueError,match='Complete a ficha visual'):
        await p.generate_reference('guard','noe',42)
