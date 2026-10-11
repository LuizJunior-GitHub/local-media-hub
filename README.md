# 🏛️ Local Media Hub (Media Forge Server)

> **High-Performance Unified Gateway & Model Context Protocol (MCP) Server for Local GPU-Accelerated Image Generation**  
> Tailored for **AMD Radeon RX 7800 XT (16 GB GDDR6)** running headless **ComfyUI** on Windows / WSL2 via **DirectML / ROCm**.

---

## 📋 Sumário
1. [Visão Geral & Arquitetura](#-visão-geral--arquitetura)
2. [Hardware Alvo & Gestão de VRAM](#-hardware-alvo--gestão-de-vram)
3. [Stack Tecnológico](#-stack-tecnológico)
4. [Estrutura do Projeto](#-estrutura-do-projeto)
5. [Instalação e Configuração](#-instalação-e-configuração)
6. [Integração MCP (Model Context Protocol)](#-integração-mcp-model-context-protocol)
7. [Endpoints da API REST & WebSockets](#-endpoints-da-api-rest--websockets)
8. [Sistema de Âncoras Consistentes](#-sistema-de-âncoras-consistentes)
9. [Configuração do ComfyUI Headless](#-configuração-do-comfyui-headless)
10. [Testes Automatizados](#-testes-automatizados)

---

## 🏛️ Visão Geral & Arquitetura

O **Local Media Hub** é um microsserviço standalone de missão crítica projetado para servir como backend de inteligência visual para múltiplos consumidores locais:
- **Apps de Quiz Bíblico e Histórico** (geração automática de ilustrações narrativas).
- **Robôs de Automação para YouTube / Shorts / TikTok** (criação de cenas em lote com alta coerência visual).
- **Agentes Autônomos de IA** (Claude Desktop, Antigravity, Cursor, etc., operando via protocolo MCP).

```
                      ┌──────────────────────────────────────────────┐
                      │            Consumidores Externos             │
                      │  (Quiz Apps, YouTube Bots, Agentes MCP, IDE) │
                      └──────────────────────┬───────────────────────┘
                                             │
                       HTTP REST / SSE / WS  │  MCP STDIO
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   LOCAL MEDIA HUB                                      │
│                                                                                        │
│  ┌─────────────────────────┐  ┌─────────────────────────┐  ┌────────────────────────┐  │
│  │   FastAPI Gateway /     │  │    MCP Python Server    │  │   Asset Storage &      │  │
│  │   REST Endpoints        │  │      (MCPServer)        │  │   Metadata Registry    │  │
│  └────────────┬────────────┘  └────────────┬────────────┘  └───────────┬────────────┘  │
│               │                            │                           │               │
│               └──────────────────────┐     │     ┌─────────────────────┘               │
│                                      ▼     ▼     ▼                                     │
│                         ┌────────────────────────────────────┐                         │
│                         │        QueueManager & Lock         │                         │
│                         │   (asyncio.Queue + GPU Lock: 1)    │                         │
│                         └─────────────────┬──────────────────┘                         │
│                                           │                                            │
│                         ┌─────────────────▼──────────────────┐                         │
│                         │          TemplateEngine            │                         │
│                         │ (Injeção de Prompts, Seeds, Nodes) │                         │
│                         └─────────────────┬──────────────────┘                         │
│                                           │                                            │
│                         ┌─────────────────▼──────────────────┐                         │
│                         │         ComfyUIClient (WS/HTTP)    │                         │
│                         │  - Submissão /prompt               │                         │
│                         │  - Monitoramento WS de Steps       │                         │
│                         │  - Download /view para outputs/    │                         │
│                         └─────────────────┬──────────────────┘                         │
└───────────────────────────────────────────┼────────────────────────────────────────────┘
                                            │ HTTP / WebSocket (127.0.0.1:8188)
                                            ▼
                        ┌────────────────────────────────────────┐
                        │      ComfyUI Headless Inference        │
                        │                                        │
                        │  GPU: AMD Radeon RX 7800 XT (16 GB)    │
                        │  Backend: DirectML / ROCm              │
                        │  Modelos: SDXL, Flux.1, IP-Adapter     │
                        └────────────────────────────────────────┘
```

---

## ⚡ Hardware Alvo & Gestão de VRAM

- **Placa de Vídeo:** AMD Radeon RX 7800 XT (16 GB GDDR6).
- **Backend de Inferência:** DirectML (no Windows nativo) ou ROCm 6.x (no WSL2 Ubuntu).
- **Trava Estrita de GPU (Zero-OOM Guarantee):**
  - Modelos SDXL com nós adicionais (IP-Adapter FaceID, ControlNet Depth) e Flux.1 Schnell quantizado utilizam entre **9 GB e 14 GB de VRAM**.
  - Executar jobs concorrentes simultaneamente na mesma GPU causaria estouro de memória (Out-Of-Memory) ou crash de driver Windows TDR.
  - O `QueueManager` implementa uma **trava de concorrência assíncrona (`asyncio.Lock`)**:
    - Requisições são aceitas e enfileiradas imediatamente no `asyncio.Queue`.
    - **Apenas 1 job pesado executa na GPU por vez.**
    - Notificações de progresso (steps e porcentagens) são propagadas em tempo real via WebSockets para os assinantes.

---

## 🛠️ Stack Tecnológico

| Componente | Tecnologia | Papel |
|---|---|---|
| **Linguagem** | Python 3.11+ / 3.13 | Base de execução assíncrona moderna |
| **API Web** | FastAPI + Uvicorn | Gateway HTTP assíncrono e WebSockets |
| **Protocolo MCP** | `mcp` SDK oficial (`MCPServer`) | Suporte a STDIO e SSE para agentes de IA |
| **Cliente de Inferência** | `aiohttp` / `websockets` | Comunicação full-async com ComfyUI |
| **Validação** | Pydantic v2 & Pydantic-Settings | Tipagem estrita de requests, responses e `.env` |
| **Processamento de Imagens** | Pillow (PIL) | Inspeção de dimensões e metadados de âncoras |
| **Testes** | Pytest + Pytest-Asyncio + HTTPX | Cobertura abrangente de rotas, fila e MCP |

---

## 📂 Estrutura do Projeto

```text
local-media-hub/
├── app/
│   ├── api/
│   │   ├── routes_generate.py       # Endpoints REST de geração (SDXL, Flux, Personagem, Cenário)
│   │   ├── routes_assets.py         # Upload e gestão de âncoras (personagens/locais)
│   │   └── routes_system.py         # Healthcheck, status da GPU/ComfyUI e fila
│   ├── core/
│   │   ├── config.py                # Configurações via pydantic-settings (.env)
│   │   └── queue_manager.py         # Fila assíncrona de jobs com trava de GPU
│   ├── engine/
│   │   ├── comfy_client.py          # Wrapper HTTP/WebSocket da API do ComfyUI
│   │   ├── template_engine.py       # Injeção de prompts/seeds nos workflows JSON
│   │   └── workflows/               # Templates de nós do ComfyUI (.json)
│   │       ├── sdxl_standard.json
│   │       ├── flux_schnell.json
│   │       ├── character_consistency.json
│   │       └── location_consistency.json
│   ├── mcp/
│   │   └── server.py                # Implementação das Tools do Model Context Protocol
│   ├── schemas/
│   │   ├── generation.py            # Modelos Pydantic (requests/responses)
│   │   └── assets.py                # Modelos de personagens e locais
│   └── services/
│       └── asset_service.py         # Armazenamento e indexação de referências
├── storage/
│   ├── outputs/                     # Imagens finalizadas geradas
│   ├── references/
│   │   ├── characters/              # Imagens âncora de personagens (ex: moises.png, moises.json)
│   │   └── locations/               # Imagens âncora de cenários (ex: templo.png, templo.json)
├── tests/                           # Suite de testes unitários e de integração
├── .env.example
├── pyproject.toml
├── requirements.txt
└── main.py                          # Ponto de entrada do serviço
```

---

## 🚀 Instalação e Configuração

### 1. Clonar e Instalar Dependências
```bash
cd local-media-hub

# Criar ambiente virtual (opcional)
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate # Linux / WSL2

# Instalar pacotes
pip install -r requirements.txt
```

### 2. Configurar Variáveis de Ambiente
Copie o template de ambiente:
```bash
copy .env.example .env
```
Ajuste os parâmetros conforme seu setup:
```ini
COMFYUI_HOST="127.0.0.1"
COMFYUI_PORT=8188
GPU_DEVICE_NAME="AMD Radeon RX 7800 XT"
GPU_VRAM_GB=16
GPU_BACKEND="directml"
MAX_CONCURRENT_GPU_JOBS=1
```

### 3. Iniciar o Servidor
#### Modo API Gateway HTTP + WebSockets (Porta 8000):
```bash
python main.py
```
O painel web interativo (Studio UI) estará acessível em:
👉 **`http://127.0.0.1:8000/`**

A documentação interativa Swagger estará acessível em:
👉 **`http://127.0.0.1:8000/docs`**

#### Modo MCP STDIO (para Claude Desktop / Agentes):
```bash
python main.py --mcp-stdio
```

---

## 🤖 Integração MCP (Model Context Protocol)

O servidor MCP expõe ferramentas nativas para agentes de IA gerarem mídia diretamente no disco local.

### Ferramentas Expostas:
1. `generate_image(prompt, engine="sdxl", width=1024, height=1024, steps=30, seed=None)`:
   - Suporta SDXL (ultrarrápido, 3-5s) e Flux.1 Schnell (composição avançada, 7-25s).
2. `generate_character_image(character_id, prompt, face_strength=0.85, seed=None)`:
   - Gera imagens do personagem bíblico/histórico mantendo consistência facial via IP-Adapter FaceID.
3. `generate_location_image(location_id, prompt, depth_strength=0.60, seed=None)`:
   - Mantém consistência estrutural e arquitetônica de locais/cenários via ControlNet Depth.
4. `list_character_anchors()`:
   - Retorna todas as âncoras de personagens cadastradas no sistema.
5. `list_location_anchors()`:
   - Retorna todas as âncoras de locais/cenários cadastradas.
6. `get_system_status()`:
   - Consulta temperatura, VRAM, conectividade do ComfyUI e status da fila.

### Configuração no Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "local-media-hub": {
      "command": "python",
      "args": [
        "d:\\GitHub\\local-media-hub\\main.py",
        "--mcp-stdio"
      ]
    }
  }
}
```

---

## 📡 Endpoints da API REST & WebSockets

### 1. Geração de Imagens
- **`POST /api/v1/generate/sdxl`**: Geração ultrarrápida com SDXL (RealVisXL / Juggernaut XL).
- **`POST /api/v1/generate/flux`**: Geração cinematográfica com Flux.1 Schnell.
- **`POST /api/v1/generate/character`**: Geração com consistência facial baseada em âncora cadastrada.
- **`POST /api/v1/generate/location`**: Geração com consistência de cenário baseada em ControlNet Depth.

> **Parâmetro `wait_for_completion`**:
> - Se `true` (padrão): a requisição aguarda o término e retorna o objeto completo com a imagem gerada (HTTP 200).
> - Se `false`: retorna imediatamente com HTTP 202 Accepted e `job_id` para pooling ou acompanhamento via WebSocket.

### 2. Acompanhamento de Fila & Jobs
- **`GET /api/v1/jobs/{job_id}`**: Retorna status atual, porcentagem e imagens finalizadas.
- **`GET /api/v1/jobs`**: Lista os jobs recentes.
- **`POST /api/v1/jobs/{job_id}/cancel`**: Cancela um job em fila ou em execução.
- **`WS /api/v1/jobs/{job_id}/ws`**: Conexão WebSocket em tempo real para receber eventos de progresso de steps (`{"progress": 0.45, "current_step": 14, "total_steps": 30}`).

### 3. Gestão de Âncoras
- **`GET /api/v1/assets`**: Lista todas as âncoras (personagens e locais).
- **`POST /api/v1/assets/characters`**: Upload multipart/form-data de nova imagem âncora de personagem.
- **`GET /api/v1/assets/characters/{id}`**: Detalhes do personagem.
- **`DELETE /api/v1/assets/characters/{id}`**: Exclusão de personagem.
- **`POST /api/v1/assets/locations`**: Upload multipart/form-data de nova âncora de cenário.

### 4. Sistema e GPU
- **`GET /api/v1/system/health`**: Healthcheck geral da API e ComfyUI.
- **`GET /api/v1/system/status`**: Informações detalhadas da GPU e VRAM.
- **`GET /api/v1/system/queue`**: Status em tempo real da trava de concorrência da GPU.
- **`POST /api/v1/system/gpu/free`**: Força coleta de lixo e descarregamento de modelos do VRAM.

---

## 🎭 Sistema de Âncoras Consistentes

Para manter coerência visual em séries, canais do YouTube e quizzes, o sistema utiliza **Âncoras**:

### Exemplo de Âncora de Personagem (`moises`):
- Imagem: `storage/references/characters/moises.png`
- Metadados: `storage/references/characters/moises.json`
- Prompt Modifier automático: `elderly biblical patriarch, grey beard, rustic linen robes, ancient desert lighting, 8k portrait`
- Nó de inferência: Injeta o vetor facial via **IP-Adapter FaceID** com peso ajustável (`face_strength`: 0.85).

### Exemplo de Âncora de Cenário (`templo_salomao`):
- Imagem: `storage/references/locations/templo.png`
- Metadados: `storage/references/locations/templo_salomao.json`
- Style Notes: `monumental stone pillars, golden accents, warm morning sunbeams through high clerestory, cinematic atmosphere`
- Nó de inferência: Extrai o mapa de profundidade via **MiDaS / ControlNet Depth** com peso ajustável (`depth_strength`: 0.60).

---

## 🔧 Configuração do ComfyUI Headless

O ComfyUI deve rodar na mesma máquina (ou rede local):

```bash
# Execução headless no Windows com DirectML:
python main.py --directml --port 8188 --listen 127.0.0.1 --dont-up-scale-preview

# Execução no WSL2 com ROCm:
python main.py --port 8188 --listen 127.0.0.1 --dont-up-scale-preview
```

### Modelos recomendados nos diretórios do ComfyUI:
- `models/checkpoints/`:
  - `juggernautXL_v9Rundiffusion.safetensors`
  - `realvisxlV40_v40Bakedvae.safetensors`
- `models/unet/`:
  - `flux1-schnell-Q8_0.gguf`
- `models/ipadapter/`:
  - `ip-adapter-faceid-plusv2_sdxl.bin`
- `models/controlnet/`:
  - `controlnet-depth-sdxl-1.0.safetensors`

---

## 🧪 Testes Automatizados

A suíte de testes cobre todas as camadas do sistema com mocks assíncronos e testes de unidade para validação completa sem necessidade da GPU ativa durante o CI/CD:

```bash
pytest -v
```

Resultado da suíte:
```text
tests/test_api_endpoints.py::test_health_check_endpoint PASSED           [  5%]
tests/test_api_endpoints.py::test_status_endpoint PASSED                 [ 10%]
tests/test_api_endpoints.py::test_assets_endpoints PASSED                [ 15%]
tests/test_api_endpoints.py::test_async_job_submission PASSED            [ 21%]
tests/test_asset_service.py::test_list_existing_assets PASSED            [ 26%]
tests/test_asset_service.py::test_save_and_delete_character PASSED       [ 31%]
tests/test_asset_service.py::test_save_and_delete_location PASSED        [ 36%]
tests/test_config.py::test_settings_defaults PASSED                      [ 42%]
tests/test_config.py::test_directories_creation PASSED                   [ 47%]
tests/test_mcp_server.py::test_mcp_server_initialization_and_tools PASSED [ 52%]
tests/test_mcp_server.py::test_mcp_call_list_anchors PASSED              [ 57%]
tests/test_mcp_server.py::test_mcp_call_system_status PASSED             [ 63%]
tests/test_queue_manager.py::test_queue_single_gpu_concurrency_lock PASSED [ 68%]
tests/test_queue_manager.py::test_queue_job_cancellation PASSED          [ 73%]
tests/test_template_engine.py::test_sdxl_workflow_injection PASSED       [ 78%]
tests/test_template_engine.py::test_flux_workflow_injection PASSED       [ 84%]
tests/test_character_workflow_injection PASSED                           [ 89%]
tests/test_location_workflow_injection PASSED                            [ 94%]
tests/test_random_seed_resolution PASSED                                 [100%]

============================= 19 passed in 3.41s ==============================
```

---

## 📄 Licença
Distribuído sob licença MIT. Projetado para máxima escalabilidade, confiabilidade e facilidade de integração em ecossistemas de agentes de IA locais.
