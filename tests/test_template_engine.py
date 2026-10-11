import pytest
from app.engine.template_engine import TemplateEngine, get_template_engine


def test_sdxl_workflow_injection():
    engine = get_template_engine()
    prompt = "Moses speaking to Pharaoh, ancient palace, 8k"
    wf, seed = engine.build_sdxl_workflow(
        prompt=prompt,
        negative_prompt="modern items, blur",
        seed=12345,
        width=1024,
        height=1024,
        steps=28,
        cfg_scale=6.5,
    )

    assert seed == 12345
    assert wf["6"]["inputs"]["text"] == prompt
    assert wf["7"]["inputs"]["text"] == "modern items, blur"
    assert wf["5"]["inputs"]["width"] == 1024
    assert wf["5"]["inputs"]["height"] == 1024
    assert wf["3"]["inputs"]["steps"] == 28
    assert wf["3"]["inputs"]["cfg"] == 6.5
    assert wf["3"]["inputs"]["seed"] == 12345


def test_flux_workflow_injection():
    engine = get_template_engine()
    prompt = "Cinematic shot of ancient Galilee, sunset"
    wf, seed = engine.build_flux_workflow(
        prompt=prompt,
        seed=9999,
        steps=4,
        guidance=3.5,
    )

    assert seed == 9999
    assert wf["5"]["inputs"]["text"] == prompt
    assert wf["6"]["inputs"]["guidance"] == 3.5
    assert wf["7"]["inputs"]["steps"] == 4
    assert wf["7"]["inputs"]["seed"] == 9999


def test_character_workflow_injection():
    engine = get_template_engine()
    wf, seed = engine.build_character_workflow(
        anchor_image_filename="moises.png",
        prompt="Moses looking at the burning bush",
        face_strength=0.88,
        seed=777,
    )

    assert seed == 777
    assert wf["2"]["inputs"]["image"] == "moises.png"
    assert wf["5"]["inputs"]["weight"] == 0.88
    assert wf["7"]["inputs"]["text"] == "Moses looking at the burning bush"
    assert wf["9"]["inputs"]["seed"] == 777


def test_location_workflow_injection():
    engine = get_template_engine()
    wf, seed = engine.build_location_workflow(
        anchor_image_filename="templo.png",
        prompt="Priests entering the temple court at dawn",
        depth_strength=0.65,
        seed=888,
    )

    assert seed == 888
    assert wf["2"]["inputs"]["image"] == "templo.png"
    assert wf["5"]["inputs"]["strength"] == 0.65
    assert wf["7"]["inputs"]["text"] == "Priests entering the temple court at dawn"
    assert wf["9"]["inputs"]["seed"] == 888


def test_random_seed_resolution():
    engine = get_template_engine()
    s1 = engine.resolve_seed(None)
    s2 = engine.resolve_seed(None)
    assert isinstance(s1, int)
    assert s1 > 0
    # Probability of two random 32-bit ints being equal is negligible
    assert s1 != s2
