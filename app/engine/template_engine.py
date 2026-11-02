import copy
import json
from pathlib import Path
import random
from typing import Any, Dict, Optional, Tuple

from app.core.config import get_settings


WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"


class TemplateEngine:
    """
    Manages loading and dynamic parameter injection into ComfyUI workflow JSON graphs.
    """

    def __init__(self):
        self.settings = get_settings()
        self._raw_templates: Dict[str, dict] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """Cache raw template JSONs in memory."""
        for name in [
            "sdxl_standard",
            "flux_schnell",
            "character_consistency",
            "location_consistency",
        ]:
            file_path = WORKFLOWS_DIR / f"{name}.json"
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    self._raw_templates[name] = json.load(f)

    def get_template_copy(self, template_name: str) -> dict:
        """Return a deep copy of a raw template."""
        if template_name not in self._raw_templates:
            file_path = WORKFLOWS_DIR / f"{template_name}.json"
            if not file_path.exists():
                raise FileNotFoundError(f"Workflow template {template_name}.json not found in {WORKFLOWS_DIR}")
            with open(file_path, "r", encoding="utf-8") as f:
                self._raw_templates[template_name] = json.load(f)

        return copy.deepcopy(self._raw_templates[template_name])

    @staticmethod
    def resolve_seed(seed: Optional[int]) -> int:
        """Resolve seed: if None, generate a cryptographically random 32-bit positive integer."""
        if seed is None:
            return random.randint(1, 2**32 - 1)
        return int(seed)

    # --------------------------------------------------------------------------
    # SDXL Standard
    # --------------------------------------------------------------------------
    def build_sdxl_workflow(
        self,
        prompt: str,
        negative_prompt: str = "ugly, deformed, disfigured, blurry, low quality, bad anatomy",
        seed: Optional[int] = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg_scale: float = 7.0,
        sampler_name: str = "dpmpp_2m",
        scheduler: str = "karras",
        checkpoint: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Inject parameters into SDXL standard workflow."""
        wf = self.get_template_copy("sdxl_standard")
        final_seed = self.resolve_seed(seed)
        ckpt_name = checkpoint or self.settings.default_sdxl_model

        # Node 4: Checkpoint
        if "4" in wf and "inputs" in wf["4"]:
            wf["4"]["inputs"]["ckpt_name"] = ckpt_name

        # Node 5: Latent Image Dimensions
        if "5" in wf and "inputs" in wf["5"]:
            wf["5"]["inputs"]["width"] = width
            wf["5"]["inputs"]["height"] = height

        # Node 6: Positive Prompt
        if "6" in wf and "inputs" in wf["6"]:
            wf["6"]["inputs"]["text"] = prompt

        # Node 7: Negative Prompt
        if "7" in wf and "inputs" in wf["7"]:
            wf["7"]["inputs"]["text"] = negative_prompt

        # Node 3: KSampler
        if "3" in wf and "inputs" in wf["3"]:
            wf["3"]["inputs"]["seed"] = final_seed
            wf["3"]["inputs"]["steps"] = steps
            wf["3"]["inputs"]["cfg"] = cfg_scale
            wf["3"]["inputs"]["sampler_name"] = sampler_name
            wf["3"]["inputs"]["scheduler"] = scheduler

        return wf, final_seed

    # --------------------------------------------------------------------------
    # Flux Schnell
    # --------------------------------------------------------------------------
    def build_flux_workflow(
        self,
        prompt: str,
        seed: Optional[int] = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 4,
        guidance: float = 3.5,
        sampler_name: str = "euler",
        scheduler: str = "simple",
        unet_name: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Inject parameters into Flux.1 Schnell workflow."""
        wf = self.get_template_copy("flux_schnell")
        final_seed = self.resolve_seed(seed)

        # Node 1: UNET (GGUF)
        if "1" in wf and "inputs" in wf["1"]:
            wf["1"]["inputs"]["unet_name"] = unet_name or self.settings.default_flux_model

        # Node 2: Dual CLIP
        if "2" in wf and "inputs" in wf["2"]:
            wf["2"]["inputs"]["clip_name1"] = self.settings.default_clip_l
            wf["2"]["inputs"]["clip_name2"] = self.settings.default_t5_xxl

        # Node 3: VAE
        if "3" in wf and "inputs" in wf["3"]:
            wf["3"]["inputs"]["vae_name"] = self.settings.default_flux_vae

        # Node 4: Dimensions
        if "4" in wf and "inputs" in wf["4"]:
            wf["4"]["inputs"]["width"] = width
            wf["4"]["inputs"]["height"] = height

        # Node 5: Prompt
        if "5" in wf and "inputs" in wf["5"]:
            wf["5"]["inputs"]["text"] = prompt

        # Node 6: Guidance
        if "6" in wf and "inputs" in wf["6"]:
            wf["6"]["inputs"]["guidance"] = guidance

        # Node 7: KSampler
        if "7" in wf and "inputs" in wf["7"]:
            wf["7"]["inputs"]["seed"] = final_seed
            wf["7"]["inputs"]["steps"] = steps
            wf["7"]["inputs"]["sampler_name"] = sampler_name
            wf["7"]["inputs"]["scheduler"] = scheduler

        return wf, final_seed

    # --------------------------------------------------------------------------
    # Character Consistency
    # --------------------------------------------------------------------------
    def build_klein_workflow(self, prompt: str, seed: Optional[int] = None,
                             width: int = 1024, height: int = 576, reference_images=None,
                             steps: int = 4) -> Tuple[Dict[str, Any], int]:
        """Official FLUX.2 Klein distilled graph with real multi-image latents.

        Each reference is VAE encoded and appended to BOTH positive and zero
        conditioning, following Comfy-Org's multi-image distilled template.
        Output framing is independent of the reference portrait/environment.
        """
        if width % 16 or height % 16 or not (256 <= width <= 2048 and 256 <= height <= 2048):
            raise ValueError("Klein dimensions must be multiples of 16 between 256 and 2048")
        if type(steps) is not int or not 1<=steps<=16:
            raise ValueError('Klein steps must be an integer between 1 and 16')
        refs = reference_images or []
        if len(refs)>6:
            raise ValueError("At most six approved reference images per scene on this GPU")
        final_seed=self.resolve_seed(seed)
        def node(kind,**inputs):return {"class_type":kind,"inputs":inputs}
        wf={"1":node("UNETLoader",unet_name=self.settings.default_klein_model,weight_dtype="default"),
            "2":node("CLIPLoader",clip_name=self.settings.default_klein_clip,type="flux2",device="default"),
            "3":node("VAELoader",vae_name=self.settings.default_klein_vae),
            "4":node("CLIPTextEncode",clip=["2",0],text=prompt),
            "5":node("ConditioningZeroOut",conditioning=["4",0]),
            "6":node("EmptyFlux2LatentImage",width=width,height=height,batch_size=1),
            "7":node("Flux2Scheduler",steps=steps,width=width,height=height),
            "8":node("KSamplerSelect",sampler_name="euler"),
            "9":node("RandomNoise",noise_seed=final_seed)}
        positive,negative=["4",0],["5",0]
        for index,filename in enumerate(refs):
            base=20+index*5
            load,scale,encode,pos,neg=map(str,range(base,base+5))
            wf[load]=node("LoadImage",image=filename)
            wf[scale]=node("ImageScaleToTotalPixels",image=[load,0],upscale_method="lanczos",megapixels=.25,resolution_steps=16)
            wf[encode]=node("VAEEncode",pixels=[scale,0],vae=["3",0])
            wf[pos]=node("ReferenceLatent",conditioning=positive,latent=[encode,0])
            wf[neg]=node("ReferenceLatent",conditioning=negative,latent=[encode,0])
            positive,negative=[pos,0],[neg,0]
        wf["10"]=node("CFGGuider",model=["1",0],positive=positive,negative=negative,cfg=1.0)
        wf["11"]=node("SamplerCustomAdvanced",noise=["9",0],guider=["10",0],sampler=["8",0],sigmas=["7",0],latent_image=["6",0])
        wf["12"]=node("VAEDecode",samples=["11",0],vae=["3",0])
        wf["13"]=node("SaveImage",images=["12",0],filename_prefix="LMH_KLEIN")
        return wf,final_seed

    def build_character_workflow(
        self,
        anchor_image_filename: str,
        prompt: str,
        negative_prompt: str = "ugly, deformed face, bad eyes, disfigured, bad anatomy, duplicate, blur",
        face_strength: float = 0.85,
        seed: Optional[int] = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg_scale: float = 7.0,
        sampler_name: str = "dpmpp_2m",
        scheduler: str = "karras",
        checkpoint: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Inject parameters into Character Consistency (IP-Adapter FaceID) workflow."""
        wf = self.get_template_copy("character_consistency")
        final_seed = self.resolve_seed(seed)

        # Node 1: Checkpoint
        if "1" in wf and "inputs" in wf["1"]:
            wf["1"]["inputs"]["ckpt_name"] = checkpoint or self.settings.default_sdxl_model

        # Node 2: LoadImage (reference anchor image)
        if "2" in wf and "inputs" in wf["2"]:
            wf["2"]["inputs"]["image"] = anchor_image_filename

        # Node 3: IPAdapterModelLoader
        if "3" in wf and "inputs" in wf["3"]:
            wf["3"]["inputs"]["ipadapter_file"] = self.settings.default_ipadapter_face_model

        # Node 5: IPAdapterFaceID
        if "5" in wf and "inputs" in wf["5"]:
            wf["5"]["inputs"]["weight"] = face_strength

        # Node 6: Latent Image
        if "6" in wf and "inputs" in wf["6"]:
            wf["6"]["inputs"]["width"] = width
            wf["6"]["inputs"]["height"] = height

        # Node 7: Positive Prompt
        if "7" in wf and "inputs" in wf["7"]:
            wf["7"]["inputs"]["text"] = prompt

        # Node 8: Negative Prompt
        if "8" in wf and "inputs" in wf["8"]:
            wf["8"]["inputs"]["text"] = negative_prompt

        # Node 9: KSampler
        if "9" in wf and "inputs" in wf["9"]:
            wf["9"]["inputs"]["seed"] = final_seed
            wf["9"]["inputs"]["steps"] = steps
            wf["9"]["inputs"]["cfg"] = cfg_scale
            wf["9"]["inputs"]["sampler_name"] = sampler_name
            wf["9"]["inputs"]["scheduler"] = scheduler

        return wf, final_seed

    # --------------------------------------------------------------------------
    # Location Consistency
    # --------------------------------------------------------------------------
    def build_location_workflow(
        self,
        anchor_image_filename: str,
        prompt: str,
        negative_prompt: str = "modern buildings, cars, artifacts, out of place objects, low quality, blur",
        depth_strength: float = 0.60,
        seed: Optional[int] = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg_scale: float = 7.0,
        sampler_name: str = "dpmpp_2m",
        scheduler: str = "karras",
        checkpoint: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Inject parameters into Location Consistency (ControlNet Depth) workflow."""
        wf = self.get_template_copy("location_consistency")
        final_seed = self.resolve_seed(seed)

        # Node 1: Checkpoint
        if "1" in wf and "inputs" in wf["1"]:
            wf["1"]["inputs"]["ckpt_name"] = checkpoint or self.settings.default_sdxl_model

        # Node 2: LoadImage
        if "2" in wf and "inputs" in wf["2"]:
            wf["2"]["inputs"]["image"] = anchor_image_filename

        # Node 3: ControlNetLoader
        if "3" in wf and "inputs" in wf["3"]:
            wf["3"]["inputs"]["control_net_name"] = self.settings.default_controlnet_depth_model

        # Node 5: ControlNetApplyAdvanced
        if "5" in wf and "inputs" in wf["5"]:
            wf["5"]["inputs"]["strength"] = depth_strength

        # Node 6: Latent Image
        if "6" in wf and "inputs" in wf["6"]:
            wf["6"]["inputs"]["width"] = width
            wf["6"]["inputs"]["height"] = height

        # Node 7: Positive Prompt
        if "7" in wf and "inputs" in wf["7"]:
            wf["7"]["inputs"]["text"] = prompt

        # Node 8: Negative Prompt
        if "8" in wf and "inputs" in wf["8"]:
            wf["8"]["inputs"]["text"] = negative_prompt

        # Node 9: KSampler
        if "9" in wf and "inputs" in wf["9"]:
            wf["9"]["inputs"]["seed"] = final_seed
            wf["9"]["inputs"]["steps"] = steps
            wf["9"]["inputs"]["cfg"] = cfg_scale
            wf["9"]["inputs"]["sampler_name"] = sampler_name
            wf["9"]["inputs"]["scheduler"] = scheduler

        return wf, final_seed


_template_engine_instance: Optional[TemplateEngine] = None


def get_template_engine() -> TemplateEngine:
    global _template_engine_instance
    if _template_engine_instance is None:
        _template_engine_instance = TemplateEngine()
    return _template_engine_instance
