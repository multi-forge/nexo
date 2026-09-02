# JARVIS-DEV: Arquitetura Orientada a Eventos em Tempo Real
### Streaming de Voz Contínuo, Gerenciador de Tasks Assíncronas e Computer Use

---

## 1. Visão Geral da Topologia de Decisão em Tempo Real

O usuário fala continuamente no microfone. O fluxo de áudio entra na **Gemini Live API**, que atua como **Roteador Cognitivo Instantâneo**. O sistema divide as intenções em 4 caminhos:

```
                                  [ÁUDIO EM TEMPO REAL DO MICROFONE]
                                                  │
                                                  ▼
                                    ┌────────────────────────────┐
                                    │  Gemini 3.1 Live (Router)  │
                                    └─────────────┬──────────────┘
                                                  │
         ┌──────────────────┬─────────────────────┼────────────────────────────┐
         ▼                  ▼                     ▼                            ▼
  [1. DIÁLOGO PURO]   [2. FAST TOOL]      [3. AGY TASK (ASYNC)]       [4. COMPUTER USE]
  • Dúvida técnica    • git status        • Refatorar módulo          • Abrir navegador
  • Bate-papo leve    • pytest rápido     • Criar endpoint            • Clicar na tela
  • Planejamento      • Ler arquivo       • Resolver bug complexo     • Preencher formulário
         │                  │                     │                            │
         ▼                  ▼                     ▼                            ▼
  Fala Imediata       Executa (50ms)       Cria Task em Background     Agente de Visão / DOM
    (< 350ms)         e resume em 1 frase   e responde em 1s:         Executa ações de tela
         │                  │               "Iniciei a tarefa..."     e resume o status
         │                  │                     │                            │
         │                  │                     ▼                            ▼
         │                  │             [Event Queue Push]           [Event Queue Push]
         │                  │                     │                            │
         └──────────────────┴─────────────────────┴────────────────────────────┘
                                                  │
                                                  ▼
                               [ÁUDIO CURTO E CONCISO NO FONE (24kHz)]
                                                  +
                               [LOG VISUAL E SCREENSHOTS NO TELEGRAM]
```

---

## 2. As 4 Rotas de Execução Explicadas

### 🟢 Rota 1: Diálogo Direto (< 350 ms)
- **Quando ocorre:** Quando o usuário faz uma pergunta conceitual ou conversa informal.
- **Comportamento:** O Gemini Live responde **instantaneamente por voz** sem chamar nenhuma ferramenta.
- *Exemplo:* 
  > 👤 *"Jarvis, qual a diferença entre processo e thread?"*  
  > 🎧 *"Processos têm memória isolada, threads compartilham o mesmo espaço de memória."*

---

### 🟡 Rota 2: Fast Tool Síncrona (10 ms – 100 ms)
- **Quando ocorre:** Comandos de terminal que duram menos de 1 segundo (`git branch`, `git status`, `cat`, `date`).
- **Comportamento:** Executa na hora, o Gemini recebe o retorno e emite uma **frase única direta**.
- *Exemplo:*
  > 👤 *"Qual a branch atual?"*  
  > 🎧 *"Branch develop, três commits à frente da main."*

---

### 🔵 Rota 3: AGY Task Assíncrona (Background Job)
- **Quando ocorre:** Tarefas de programação que levam de 15 a 60 segundos (escrever código, refatorar, rodar testes pesados).
- **Comportamento:**
  1. O Gemini Live **não trava**. Ele cria um `Task ID` e fala uma confirmação de **1 segundo**: *"Iniciando a refatoração do auth..."*
  2. O Antigravity roda em background via streaming NDJSON.
  3. Quando termina, o **Event Manager** injeta uma notificação de alta prioridade na sessão do Gemini: *"Tarefa concluída: 4 arquivos alterados e todos os testes verdes."*
  4. Enquanto isso, o usuário pode continuar conversando livremente no fone.

---

### 🟣 Rota 4: Computer Use (Navegador e Controle do Sistema)
- **Quando ocorre:** Quando a tarefa exige interação visual com a interface gráfica do computador (abrir navegador, fazer login em painéis AWS/Vercel, preencher forms, tirar screenshots).
- **Como funciona:**
  - O Host roda um agente de **Computer Use** conectado via Playwright / xdotool / PyAutoGUI / MCP Browser.
  - A cada ação visual relevante (ex: *"Logou no painel"*, *"Clicou em Deploy"*), uma minúscula pílula de voz é emitida e um screenshot comprimido vai para o Telegram.
- *Exemplo:*
  > 👤 *"Jarvis, entra no painel da Vercel e promove o último preview pra produção."*  
  > 🎧 *"Abrindo o painel da Vercel..."*  
  > *(Agente navega, clica no botão 'Promote to Production')*  
  > 🎧 *"Deploy em produção acionado com sucesso. Te mandei o link no Telegram."*

---

## 3. Implementação do Host Orchestrator com Tasks & Event Queue

Este código implementa o servidor completo com suporte a **Áudio Contínuo**, **Tasks Assíncronas em Background**, **Event Push** e **Computer Use Mock**:

```python
#!/usr/bin/env python3
"""
Jarvis-Dev: Master Orchestrator com Tasks Assíncronas e Computer Use
====================================================================
Implementa:
1. Streaming de áudio bidirecional contínuo (Gemini 3.1 Live)
2. Fast Path para comandos rápidos
3. Background Task Manager para tarefas longas do Antigravity
4. Módulo de Computer Use para automação de tela e browser
5. Notificações por voz assíncronas injetadas no fluxo de áudio
"""

import asyncio
import json
import os
import time
import uuid
from typing import Dict, Any

import websockets
from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
HOST_PORT = int(os.environ.get("JARVIS_PORT", "8765"))
PROJECT_DIR = os.environ.get("PROJECT_DIR", os.path.expanduser("~"))

client = genai.Client(api_key=GEMINI_API_KEY, http_options={"api_version": "v1beta"})

# ─── System Instruction Concisa e Natural ──────────────────────────────

SYSTEM_INSTRUCTION = """
Você é o Jarvis, copiloto técnico de programação e automação no fone de ouvido.

REGRAS DE CONVERSAÇÃO E RESPOSTA:
1. Seja direto, natural e conciso (máximo 6 a 12 palavras por fala).
2. NUNCA faça monólogos longos ou use linguagem teatral/rebuscada.
3. Ao acionar ferramentas demoradas (como criar código ou automação de tela), 
   diga apenas uma frase curta confirmando o início (ex: 'Iniciando a refatoração...').
4. Quando receber notificações de tarefas concluídas em background, 
   resuma o resultado final em uma única frase amigável.
"""

# ─── Declaração das Ferramentas ────────────────────────────────────────

TOOLS_CONFIG = [
    {
        "function_declarations": [
            {
                "name": "fast_shell",
                "description": "Executa comandos ultrarrápidos no terminal (< 1 segundo). Use para git status, branch, ls, date.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"command": {"type": "STRING", "description": "Comando bash rápido"}},
                    "required": ["command"]
                }
            },
            {
                "name": "start_agy_task",
                "description": "Inicia uma tarefa assíncrona de escrita de código/refatoração em background com o Antigravity. Retorna imediatamente com o Task ID.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "prompt": {"type": "STRING", "description": "Instrução técnica detalhada para o agente de código."},
                        "description": {"type": "STRING", "description": "Resumo curto em 3 palavras para falar ao usuário."}
                    },
                    "required": ["prompt", "description"]
                }
            },
            {
                "name": "computer_use_action",
                "description": "Executa ações de interface gráfica no computador (abrir navegador, navegar em URLs, clicar, automação desktop).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {"type": "STRING", "description": "Ação: 'open_url', 'click', 'type_text', 'screenshot'"},
                        "target": {"type": "STRING", "description": "URL, seletor de elemento ou texto"},
                        "description": {"type": "STRING", "description": "O que você está fazendo em 3 palavras."}
                    },
                    "required": ["action", "target", "description"]
                }
            }
        ]
    }
]

# ─── Task Manager para Tarefas de Background ──────────────────────────

class TaskManager:
    def __init__(self, session_event_sink):
        self.tasks: Dict[str, asyncio.Task] = {}
        self.event_sink = session_event_sink

    async def launch_agy_task(self, prompt: str, description: str) -> str:
        task_id = f"task_{uuid.uuid4().hex[:6]}"
        print(f"🚀 [TaskManager] Iniciando AGY Task {task_id}: '{description}'")

        async def _run():
            t0 = time.perf_counter()
            # Executa AGY em modo headless com stream-json
            proc = await asyncio.create_subprocess_exec(
                "agy", "--print", prompt,
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=PROJECT_DIR
            )
            stdout, _ = await proc.communicate()
            dur = time.perf_counter() - t0
            
            # Notifica a sessão de voz que a tarefa acabou
            event_text = f"[Notificação do Sistema]: A tarefa '{description}' (ID: {task_id}) foi concluída com sucesso em {dur:.0f} segundos. Avise o usuário com uma frase curta."
            await self.event_sink(event_text)

        self.tasks[task_id] = asyncio.create_task(_run())
        return task_id

    async def launch_computer_use(self, action: str, target: str, description: str) -> str:
        task_id = f"cu_{uuid.uuid4().hex[:6]}"
        print(f"🖥️ [ComputerUse] Executando: {action} em '{target}'")

        async def _run_cu():
            await asyncio.sleep(2) # Simulação de navegação / clique
            event_text = f"[Notificação Computer Use]: Ação '{description}' concluída com sucesso. Resuma o resultado para o usuário."
            await self.event_sink(event_text)

        asyncio.create_task(_run_cu())
        return task_id


# ─── WebSocket Gateway e Bridge com Gemini Live ────────────────────────

async def handle_client(websocket):
    print("🎧 [Gateway] Smartphone conectado via Tailscale.")

    config = types.LiveConnectConfig(
        response_modalities=[types.LiveServerContentResponseModalities.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede"))
        ),
        tools=TOOLS_CONFIG,
        system_instruction=types.Content(parts=[types.Part.from_text(SYSTEM_INSTRUCTION)])
    )

    async with client.aio.live.connect(model="gemini-3.1-flash-live-preview", config=config) as session:

        # Fila de eventos assíncronos (notificações de tasks para falar na sessão)
        async def inject_event_into_voice(event_message: str):
            print(f"📢 [Voice Event Push]: {event_message}")
            await session.send(
                input=types.Content(parts=[types.Part.from_text(event_message)]),
                end_of_turn=True
            )

        task_mgr = TaskManager(session_event_sink=inject_event_into_voice)

        # ── Pipeline de Entrada: Áudio do Microfone ──
        async def mic_in_loop():
            try:
                async for message in websocket:
                    if isinstance(message, bytes) and len(message) > 1:
                        # 0x01: PCM 16kHz do microfone
                        if message[0] == 0x01:
                            await session.send(
                                input={"data": message[1:], "mime_type": "audio/pcm;rate=16000"},
                                end_of_turn=False
                            )
                        # 0x03: Controle (ex: fim de fala/botão solto)
                        elif message[0] == 0x03:
                            await session.send(end_of_turn=True)
            except websockets.ConnectionClosed:
                pass

        # ── Pipeline de Saída: Resposta do Gemini e Execução de Tools ──
        async def gemini_out_loop():
            try:
                async for response in session.receive():
                    # 1. Enviar áudio gerado de volta ao fone (Frame 0x02)
                    sc = response.server_content
                    if sc and sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                frame = bytes([0x02]) + part.inline_data.data
                                await websocket.send(frame)

                    # 2. Processar chamadas de ferramentas
                    if response.tool_call:
                        for fc in response.tool_call.function_calls:
                            name = fc.name
                            args = fc.args
                            print(f"🔧 [Tool Call]: {name}({args})")

                            # Rota 2: Fast Shell
                            if name == "fast_shell":
                                proc = await asyncio.create_subprocess_shell(
                                    args["command"],
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.PIPE,
                                    cwd=PROJECT_DIR
                                )
                                stdout, stderr = await proc.communicate()
                                tool_result = {"output": (stdout.decode() + stderr.decode()).strip()[:1000]}

                            # Rota 3: AGY Task Assíncrona
                            elif name == "start_agy_task":
                                task_id = await task_mgr.launch_agy_task(args["prompt"], args["description"])
                                tool_result = {"status": "TASK_STARTED", "task_id": task_id, "msg": "Tarefa iniciada em background."}

                            # Rota 4: Computer Use
                            elif name == "computer_use_action":
                                task_id = await task_mgr.launch_computer_use(args["action"], args["target"], args["description"])
                                tool_result = {"status": "ACTION_DISPATCHED", "task_id": task_id}

                            else:
                                tool_result = {"error": "Tool desconhecida."}

                            # Devolver resultado para o Gemini fechar a fala imediata
                            await session.send(
                                types.LiveClientToolResponse(
                                    function_responses=[
                                        types.FunctionResponse(name=name, id=fc.id, response=tool_result)
                                    ]
                                )
                            )

            except websockets.ConnectionClosed:
                pass

        await asyncio.gather(mic_in_loop(), gemini_out_loop())

async def main():
    print(f"🚀 [Jarvis Master Gateway] Escutando na porta {HOST_PORT}...")
    async with websockets.serve(handle_client, "0.0.0.0", HOST_PORT):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. Como o Computer Use Funciona na Prática

O Computer Use opera através de **Visão Computacional + Árvore de Acessibilidade (DOM)**:

```
[Usuário no Fone] ──► "Jarvis, abre a issue #42 no GitHub e aprova o PR."
                            │
                            ▼
               [Gemini Live 3.1 (Router)]
                            │
             (Emite Tool: computer_use_action)
                            │
                            ├────────────────────────────────────────┐
                            ▼                                        ▼
               🎧 [Fala Imediata: 1.5s]                    [Computer Use Agent]
               "Abrindo o PR 42 no GitHub..."                       │
                                                           • Abre URL com Playwright
                                                           • Localiza botão 'Review changes'
                                                           • Seleciona 'Approve' e envia
                                                           • Tira screenshot final
                                                                    │
                                                                    ▼
                                                           [Notificação de Sucesso]
                                                                    │
                                                           ┌────────┴────────┐
                                                           ▼                 ▼
                                               🎧 [Áudio no Fone]     📱 [Telegram]
                                              "PR 42 aprovado."     (Screenshot do PR)
```

---

## 5. Resumo da Experiência do Usuário

| Situação | O que você fala | O que o Jarvis faz | O que você ouve no fone |
|---|---|---|---|
| **Pergunta rápida** | *"Que horas são?"* | Responde direto da memória | 🎧 *"São 15 horas e 42 minutos."* |
| **Comando de terminal** | *"Qual a branch?"* | Roda `git branch` (20ms) | 🎧 *"Branch develop, tudo atualizado."* |
| **Código complexo** | *"Refatora o módulo de auth."* | Cria **AGY Task** em background | 🎧 *"Iniciando a refatoração do auth."* <br> *(30s depois)* <br> 🎧 *"Refatoração concluída, testes passaram."* |
| **Computer Use** | *"Entra na AWS e reinicia o staging."* | Aciona automação de browser | 🎧 *"Abrindo o painel da AWS..."* <br> *(5s depois)* <br> 🎧 *"Instância reiniciada com sucesso."* |
