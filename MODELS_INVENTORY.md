# 📦 Inventário e Registro de Modelos do Local Media Hub

Este documento registra todos os modelos de IA utilizados pelo **Local Media Hub** com ComfyUI, incluindo seus caminhos de destino, tamanhos aproximados e procedimentos de reinstalação quando o usuário desejar voltar a utilizá-los localmente.

---

## 📋 Lista de Modelos Removidos para Economia de Espaço

| Categoria | Arquivo | Tamanho | Caminho Relativo (`ComfyUI/models/`) |
|---|---|---|---|
| **Checkpoint SDXL** | `juggernautXL_v9Rundiffusion.safetensors` | ~6.62 GB | `checkpoints/` |
| **UNet / Diffusion** | `flux1-schnell-fp8-e4m3fn.safetensors` | ~11.07 GB | `unet/` |
| **UNet / Diffusion** | `flux1-schnell-Q4_K_S.gguf` | ~6.32 GB | `unet/` |
| **UNet / Diffusion** | `qwen-image-edit-2511-Q4_K_M.gguf` | ~12.34 GB | `unet/` |
| **Diffusion Models** | `flux-2-klein-4b-fp8.safetensors` | ~3.79 GB | `diffusion_models/` |
| **Text Encoder** | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | ~8.74 GB | `text_encoders/` |
| **Text Encoder** | `qwen_3_4b_fp8_mixed.safetensors` | ~5.25 GB | `text_encoders/` |
| **CLIP / Text** | `t5xxl_fp8_e4m3fn.safetensors` | ~4.56 GB | `clip/` |
| **CLIP / Text** | `clip_l.safetensors` | ~0.23 GB | `clip/` |
| **VAE** | `ae.safetensors` (Flux VAE) | ~0.31 GB | `vae/` |
| **VAE** | `flux2-vae.safetensors` | ~0.31 GB | `vae/` |
| **VAE** | `qwen_image_vae.safetensors` | ~0.24 GB | `vae/` |
| **VAE Approx** | `taef1_encoder.safetensors` | ~2.5 MB | `vae_approx/` |
| **VAE Approx** | `taef1_decoder.safetensors` | ~2.5 MB | `vae_approx/` |
| **VAE Approx** | `taesdxl_encoder.safetensors` | ~2.5 MB | `vae_approx/` |
| **VAE Approx** | `taesdxl_decoder.safetensors` | ~2.5 MB | `vae_approx/` |
| **VAE Approx** | `taesd_encoder.safetensors` | ~2.5 MB | `vae_approx/` |
| **VAE Approx** | `taesd_decoder.safetensors` | ~2.5 MB | `vae_approx/` |

**Espaço total liberado no SSD:** ~59.8 GB

---

## 🔄 Como Reinstalar no Futuro

### 1. Modelos Oficiais de Referência (Flux-Klein & Qwen)
O repositório já contém um script automatizado com validação de hash SHA256:
```bash
python scripts/install_reference_models.py
```
Esse script baixa e valida diretamente:
- `flux-2-klein-4b-fp8.safetensors`
- `qwen_3_4b_fp8_mixed.safetensors`
- `flux2-vae.safetensors`

### 2. Modelos Hugging Face / Civitai adicionais
- **Juggernaut XL v9:** Baixar via Hugging Face ou Civitai para `C:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models\checkpoints\`
- **Flux Schnell GGUF / FP8:** Baixar via repositório Comfy-Org / city96 para `C:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models\unet\`
- **Clip T5 / Clip L:** Baixar via repositório Comfy-Org para `C:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models\clip\`
- **Qwen Image Edit:** Baixar via Hugging Face para `C:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models\unet\` e encoders correspondentes.
