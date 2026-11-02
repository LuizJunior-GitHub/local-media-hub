# Geração local pelo MCP

Configuração validada em 11/10/2026: Windows, RX 7800 XT (16 GB), ComfyUI 0.39.0, PyTorch 2.9.1+ROCm 7.2.1. A inferência e os PNGs ficam no PC. Não há chamada a API paga de imagens; o uso do Codex/Antigravity segue as contas já utilizadas.

## Conexão

O servidor `local-media-hub` está registrado no Codex como stdio:

```powershell
codex mcp add local-media-hub -- C:\Users\JUNINHO\AppData\Local\Programs\Python\Python313\python.exe D:\GitHub\local-media-hub\mcp_bridge.py
```

O bridge usa somente `http://127.0.0.1:8000`, sem instanciar outro worker. O Hub encaminha para `127.0.0.1:8188`. `get_system_status` e `get_capabilities` iniciam os serviços locais já instalados, se necessário, sob trava de inicialização e com janelas ocultas. Inicialização longa devolve erro explícito sem submeter imagem; consulte novamente. O atalho Local Media Hub continua disponível.

Para confirmar a configuração: `codex mcp get local-media-hub`. Conversas já iniciadas podem precisar ser reabertas para carregar a ferramenta nova. O protocolo stdio foi comprovado com o cliente oficial do SDK; isso não prova que uma conversa antiga atualizou seu catálogo de ferramentas.

## Uso

1. Consultar `get_system_status` e `get_capabilities`.
2. `generate_image`: prompt, `request_id` estável e seed explícita. Flux: quatro passos, 1024×576; SDXL: 30 passos. A ferramenta retorna imediatamente `job_id`.
3. Consultar `get_job`. Não repetir geração por demora de monitoramento. Se repetir o pedido com os mesmos parâmetros/request_id, o Hub retorna o mesmo job.
4. Exibir o PNG pelo caminho absoluto retornado.

Para quizzes, `import_quiz_manifest` aceita Manifesto v1 (`scenes/sourceIndex`) e schemaVersion 1 (`timeline/images`), valida os quatro slots e salva no disco. `generate_quiz_slot` processa um slot de cada vez. Falha/cancelamento confirmado permite nova tentativa explícita: `retry_failed=true`, com `attempt_id` novo e estável. Não há retry automático. `export_quiz_project` entrega `001P.png`, `001R.png`, `001P_bg.png`, `001R_bg.png` e `production-index.json`. Reimportar o mesmo projeto preserva os registros. Para uma nova versão criativa, use outro project_id.

Arquivos nativos são 1024×576 no fluxo de kit. Exportação 1920×1080 usa Lanczos e é indicada como redimensionada no índice; não equivale a geração nativa Full HD.

## Recursos comprovados e limites

- Flux Schnell GGUF Q4_K_S, CLIP-L, T5 fp8 e AE: quatro imagens reais por MCP em 27,06 / 23,30 / 21,37 / 24,35 segundos. Tempos dependem do prompt e do carregamento dos pesos.
- Juggernaut XL instalado e workflow disponível; desempenho por este bridge não foi medido neste teste.
- FLUX.2 Klein 4B fp8, Qwen 3 4B mixed fp8 e Flux2 VAE instalados e verificados pelos hashes oficiais. O grafo aplica imagens reais de personagens, lugares e objetos com ReferenceLatent, sem substituir os vínculos por texto. Seis referências do Êxodo e cenas 003P, 003R, 023R concluídas pelo MCP nativo; 023R usa três imagens de referência. Identidade e fidelidade editorial precisam de revisão visual; não há garantia de rosto idêntico em toda cena.
- Modo simulado desativado. Falha ou backend offline não produz imagem de demonstração.
- Uma fila por Hub e um job ativo por vez. O Hub deve rodar como um único processo, sem múltiplos workers.
- Jobs e request_ids são salvos atomicamente em `storage/jobs`; projetos em `storage/projects`. Não são enviados ao Git. PNGs/modelos seguem ignorados.
- Timeout de espera não marca a geração como falha. O monitor mantém a propriedade do job durante falhas transitórias de rede.
- Após interrupção do Hub, jobs ainda em fila são marcados como falhos antes do envio e permitem retry explícito. Jobs em processamento ficam `uncertain`, bloqueando novos trabalhos. `reconcile_job` consulta o prompt_id original e recupera o resultado da história do ComfyUI; quando a confirmação do envio não chegou, tenta identificar o grafo exato no histórico. Se também houver perda do histórico, é necessária inspeção manual: não há reenvio automático.
- Progresso de passos vem do WebSocket, filtrado pelo prompt_id. A consulta durável de história/fila continua mesmo se esse canal de progresso cair.
- Cancelamento ativo é um pedido de interrupção; consultar o status até terminal. Cancelamento incerto não é anunciado como concluído. Não iniciar pedidos fora do Hub durante produção.
- Se o motor ficar inacessível durante 60 segundos, o job fica incerto e a produção para; não aguarda indefinidamente. A reconciliação também verifica PNGs originais do ComfyUI com grafo exato nos metadados antes de recuperar um resultado. Aarón foi recuperado assim, sem gerar novamente.
- Qualidade e fidelidade bíblica exigem revisão visual. O kit da arca é um teste técnico da integração, não uma ilustração editorial aprovada.

## Verificação reproduzível

```powershell
python -m pytest -q
python scripts/verify_mcp.py
```

O segundo comando gera um kit técnico na primeira execução. Execuções posteriores usam os mesmos jobs e não geram novamente. Evidência local: `work_codex/mcp-verification.json`. Para consultar a galeria: http://127.0.0.1:8000/.

## Tela e MCP compartilhados

### Fichas visuais de personagens

O botão Ficha visual edita idade aparente, pele, rosto, olhos, cabelo, barba, constituição, roupa e marcas de identidade. São escolhas artísticas, não afirmações sobre fisionomias históricas documentadas. O prompt de referência é compilado como retrato neutro, sem misturar construção, batalha ou cenário; o prompt importado fica preservado como origem. Nas cenas, as características de identidade são adicionadas junto às imagens aprovadas, enquanto ação e figurino seguem o roteiro.

O MCP usa edit_quiz_item(target=reference, changes.visual_profile). Campos: context, apparent_age, skin, face, eyes, hair, facial_hair, build, clothing, distinctive_features, avoid. expected.visual_profile detecta conflito com o editor humano. Alterar a ficha retira a aprovação e preserva todas as imagens. clear_approval=true também retira uma aprovação. Importação aceita visualProfile/visual_profile em cada referência de personagem; estado JSON preserva a ficha.

configure_quiz_project(changes.require_character_profiles=true) exige idade, pele, rosto, olhos, cabelo, barba e roupa antes de gerar novas referências. Está ligado no projeto quiz-facil-40-local, com revisão manual. Noé/Adão têm fichas preenchidas e duas novas variações pelo MCP, ainda sem aprovação. Outros personagens desse projeto precisam completar a ficha antes de gerar. Referências com vários personagens devem ser desmembradas ou receber um planejamento próprio; uma ficha individual gera um retrato de uma pessoa.

As miniaturas agora abrem a imagem ampliada; Aprovar é um botão separado. Ficha preenchida não significa imagem aprovada ou anatomia correta. Examine a imagem antes de aprovar. Esta implementação passou em 41 testes.

Abra http://127.0.0.1:8000/quiz. Projeto, Personagens, Ambiente, Kits, Geração e Downloads usam os mesmos serviços e arquivos do MCP. A tela atualiza o projeto a cada cinco segundos, preservando editores abertos. Mudanças manuais são lidas pelo MCP; mudanças do MCP aparecem na tela. A intervenção em uma imagem já submetida afeta a próxima tentativa; o job enviado mantém os parâmetros originais.

Use `get_quiz_project`, `configure_quiz_project` e `edit_quiz_item` para consultar/editar a produção. `generate_quiz_reference`, `import_quiz_reference_image` e `approve_quiz_reference` controlam imagens e aprovações. `start_quiz_batch` aceita etapas references/scenes/all e uma lista opcional de slots; `pause_quiz_batch` impede novos inícios. Para nova versão de cena concluída, use regenerate=true e attempt_id novo. A imagem anterior fica registrada. A tela oferece as mesmas operações, incluindo geração individual, aprovação, seleção geral/exceções, revisão, pausa e interrupção.

O estado JSON restaura escolhas no projeto existente neste PC, após pausar a produção. Preserva IDs de jobs e arquivos; não é um backup portátil de imagens/modelos. O ZIP contém cenas exportadas e índice. O backup completo do trabalho é a pasta storage (referências, imagens e diários), separado do Git. Código está salvo no repositório local; verificar git status antes de afirmar que foi commitado ou enviado ao remoto.

## Rotina de produção pelo MCP, sem automação de tela

Use start_quiz_batch para os slots novos selecionados, consulte os jobs existentes e exporte uma vez. Para correções, envie apenas os slots alterados com regenerate=true e attempt_id estável por tentativa. Reutilize retratos e fundos concluídos. Não abrir/operar telas para gerar, importar ou conferir quadros.

No Quiz, quiz_import_media_batch importa uma pasta com todas as imagens alteradas em uma chamada; quiz_render_preview confere o quadro real sem selecionar slides na tela. Os nomes 001P/001R correspondem à posição do par de pergunta/resposta no projeto Quiz, não ao ID da pergunta no manifesto original. Ao importar um subconjunto, prepare nomes para as posições reais e confira os slideIds retornados. Não reenviar a pasta inteira quando só duas imagens mudaram.

Rodada da arca de 11/10/2026: duas imagens Klein/4 passos, 37,66 segundos de motor, 75,86 segundos desde o envio até ambas importadas, 84,32 segundos até os dois quadros renderizados, incluindo orquestração e revisão visual. Não usar o tempo do motor como tempo total, nem extrapolar esta rodada para um vídeo inteiro ou partida fria. Evidência: outputs/simulacao_rotina_local_20261011/rodada_arca_mcp.json no workspace QUIZ ES.

Para objetos monumentais, especifique proporção com pessoa, mesmo plano de profundidade, plano geral e escala de andaimes. O teste aumentou o volume da arca, mas Noé ainda ficou maior que a proporção solicitada. Não aprovar automaticamente por melhora parcial nem prometer que instruções numéricas sejam obedecidas pelo modelo.
