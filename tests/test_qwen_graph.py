import pytest
from app.engine.qwen_graph import build_qwen_edit_workflow
from app.core.config import get_settings

def test_qwen_binds_all_real_images_to_text_and_latents():
    graph,seed=build_qwen_edit_workflow(get_settings(),'A new scene',1313,reference_images=['face.png','room.png'])
    assert seed==1313
    assert [n['inputs']['image'] for n in graph.values() if n['class_type']=='LoadImage']==['face.png','room.png']
    for text_node in ['4','5']:
        assert graph[text_node]['inputs']['image1']==['21',0]
        assert graph[text_node]['inputs']['image2']==['26',0]
    assert sum(n['class_type']=='ReferenceLatent' for n in graph.values())==4
    assert graph['9']['inputs']['conditioning']==['28',0]
    assert graph['10']['inputs']['conditioning']==['29',0]
    assert graph['11']['inputs']['steps']==40
    assert graph['11']['inputs']['cfg']==4
    assert graph['6']['inputs']=={'width':1024,'height':576,'batch_size':1}
    assert graph['12']['class_type']=='VAEDecodeTiled'

def test_qwen_rejects_extra_images_instead_of_silently_dropping_links():
    with pytest.raises(ValueError,match='none may be discarded'):
        build_qwen_edit_workflow(get_settings(),'A scene',1,reference_images=['1','2','3','4'])
