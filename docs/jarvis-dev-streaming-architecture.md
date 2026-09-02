# Jarvis-Dev: Streaming de Progresso para TTS em Tempo Real

## Descoberta: `--output-format stream-json`

O AGY emite **eventos NDJSON em tempo real** via stdout. Cada linha é um JSON com a estrutura:

```json
{"event": "<tipo>", "<tipo>": { ...payload... }}
```

## Eventos Capturados (Teste Real)

### 1. `init` — Handshake inicial
```json
{
  "event": "init",
  "conversation_id": "ef33ca8f-...",
  "init": {
    "cwd": "/data/data/com.termux/files/home",
    "tools": ["find_by_name", "write_to_file", "run_command", ...],
    "permission_mode": "always-proceed"
  }
}
```
**🔊 TTS:** *"Tarefa iniciada."*

---

### 2. `step_update` — Eventos granulares de progresso

#### Tool ACTIVE (começou a executar uma ferramenta):
```json
{
  "event": "step_update",
  "step_update": {
    "step_index": 2,
    "state": "ACTIVE",
    "step_type": "tool",
    "tool_name": "find_by_name",
    "tool_info": {
      "name": "find_by_name",
      "parameters": {"Pattern": "*.py", "SearchDirectory": "/home"}
    }
  }
}
```
**🔊 TTS:** *"Buscando arquivos Python..."*

#### Tool DONE (ferramenta terminou):
```json
{
  "event": "step_update",
  "step_update": {
    "step_index": 2,
    "state": "DONE",
    "step_type": "tool",
    "tool_name": "find_by_name",
    "duration_seconds": 1.339,
    "tool_info": {
      "output": "build_pdf.py\nfibonacci_benchmark.py"
    }
  }
}
```
**🔊 TTS:** *"Encontrei 2 arquivos, 1.3 segundos."*

#### Agent Response (streaming de texto, chunked):
```json
{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Pr"}}
{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"onto! Aqui está..."}}
```
**🔊 TTS:** Buffer os deltas até pontuação final (`.`, `!`, `\n`) e fala a frase completa.

---

### 3. `result` — Fim da execução
```json
{
  "event": "result",
  "result": {
    "status": "SUCCESS",
    "response": "Pronto! Aqui está o resumo...",
    "duration_seconds": 17.96,
    "num_turns": 1,
    "usage": {
      "input_tokens": 17696,
      "output_tokens": 761
    }
  }
}
```
**🔊 TTS:** *"Tarefa concluída em 18 segundos."*

---

## Arquitetura Proposta: Pipeline de Narração TTS

```
agy --print "..." --output-format stream-json --dangerously-skip-permissions
         │
         │ stdout (NDJSON linha a linha)
         ▼
┌─────────────────────────────────┐
│   Stream Event Parser (async)   │
│                                 │
│  Classifica cada evento em:     │
│  • tool_start  → narração curta │
│  • tool_done   → resultado      │
│  • text_delta  → buffer frases  │
│  • result      → resumo final   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│   TTS Summarizer / Narrator     │
│                                 │
│  Regras de narração:            │
│  1. Tool ACTIVE → frase ação    │
│  2. Tool DONE → resultado curto │
│  3. Text deltas → buffer até    │
│     sentença completa           │
│  4. Suppress text se próximo    │
│     tool_call chega em <2s      │
│  5. Result → "Pronto, X segs"  │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Gemini Live Session (áudio)    │
│  ou Fila de TTS local           │
└─────────────────────────────────┘
```

## Mapa de Narração por Tool

| `tool_name` | Narração ACTIVE | Narração DONE |
|---|---|---|
| `find_by_name` | *"Buscando arquivos..."* | *"Encontrei N arquivos."* |
| `grep_search` | *"Pesquisando no código..."* | *"N ocorrências encontradas."* |
| `view_file` | *"Lendo arquivo X..."* | *"Arquivo lido, N linhas."* |
| `write_to_file` | *"Criando arquivo X..."* | *"Arquivo criado."* |
| `replace_file_content` | *"Editando arquivo X..."* | *"Edição aplicada."* |
| `run_command` | *"Executando: comando..."* | *"Saída: resumo..."* |
| `search_web` | *"Pesquisando na web..."* | *"Resultados obtidos."* |

## Implementação: Stream Parser para o Orquestrador

```python
import asyncio
import json

TOOL_NARRATIONS = {
    "find_by_name":         ("Buscando arquivos",    lambda o: f"{len(o.split(chr(10)))} arquivos encontrados"),
    "grep_search":          ("Pesquisando no código", lambda o: f"{len(o.split(chr(10)))} resultados"),
    "view_file":            ("Lendo arquivo",         lambda o: "Arquivo lido"),
    "write_to_file":        ("Criando arquivo",       lambda o: "Arquivo criado"),
    "replace_file_content": ("Editando código",       lambda o: "Edição aplicada"),
    "run_command":          ("Executando comando",    lambda o: _summarize_cmd(o)),
    "search_web":           ("Pesquisando na web",    lambda o: "Resultados obtidos"),
}

def _summarize_cmd(output: str) -> str:
    lines = output.strip().split("\n")
    if len(lines) <= 2:
        return output.strip()[:100]
    return f"{len(lines)} linhas de saída"


async def stream_agy_with_narration(prompt: str, narrate_callback):
    """
    Executa o AGY em stream-json e chama narrate_callback(text)
    para cada evento relevante que deve ser falado via TTS.
    """
    proc = await asyncio.create_subprocess_exec(
        "agy", "--print", prompt,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--print-timeout", "5m0s",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    text_buffer = ""
    final_result = None

    async for raw_line in proc.stdout:
        line = raw_line.decode().strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        ev_type = event.get("event")

        if ev_type == "init":
            await narrate_callback("Tarefa iniciada.")

        elif ev_type == "step_update":
            su = event["step_update"]
            state = su.get("state")
            step_type = su.get("step_type")
            tool_name = su.get("tool_name", "")

            # ── Tool events ──
            if step_type == "tool":
                narr = TOOL_NARRATIONS.get(tool_name)

                if state == "ACTIVE" and narr:
                    # Extrair parâmetro mais relevante para contexto
                    params = su.get("tool_info", {}).get("parameters", {})
                    target = (params.get("TargetFile", "") or
                              params.get("CommandLine", "") or
                              params.get("Pattern", "") or
                              params.get("Query", ""))
                    if target:
                        short = target.split("/")[-1][:40]
                        await narrate_callback(f"{narr[0]}: {short}")
                    else:
                        await narrate_callback(f"{narr[0]}...")

                elif state == "DONE" and narr:
                    output = su.get("tool_info", {}).get("output", "")
                    duration = su.get("duration_seconds", 0)
                    summary = narr[1](output)
                    await narrate_callback(f"{summary}. {duration:.1f} segundos.")

            # ── Text streaming ──
            elif step_type == "agent_response":
                delta = su.get("text_delta", "")
                if delta:
                    text_buffer += delta
                    # Flush no TTS quando encontrar fim de sentença
                    while any(sep in text_buffer for sep in [".\n", "!\n", "?\n", "\n\n"]):
                        for sep in [".\n", "!\n", "?\n", "\n\n"]:
                            if sep in text_buffer:
                                sentence, text_buffer = text_buffer.split(sep, 1)
                                sentence = sentence.strip()
                                if len(sentence) > 10:
                                    await narrate_callback(sentence + sep[0])
                                break

        elif ev_type == "result":
            res = event["result"]
            status = res.get("status", "UNKNOWN")
            duration = res.get("duration_seconds", 0)
            if status == "SUCCESS":
                await narrate_callback(
                    f"Tarefa concluída com sucesso em {duration:.0f} segundos."
                )
            else:
                await narrate_callback(f"Tarefa falhou: {status}")
            final_result = res

    await proc.wait()
    return final_result
```

## Integração com o Orquestrador Jarvis-Dev

No `dispatch_tool` do orquestrador, substituir o `run_antigravity` por:

```python
elif name == "run_antigravity":
    prompt = args.get("prompt")

    async def narrate(text):
        # Envia o texto como áudio via Gemini Live session
        # OU injeta diretamente na fila de TTS local
        await send_tts_to_phone(text)
        await log_to_telegram(f"🗣 {text}")

    result = await stream_agy_with_narration(prompt, narrate)
    return {"result": result.get("response", "")[:1000] if result else "Timeout"}
```

> [!IMPORTANT]
> Com essa arquitetura, o usuário **ouve em tempo real** no fone:
> 1. *"Tarefa iniciada."*
> 2. *"Buscando arquivos: *.py"*
> 3. *"2 arquivos encontrados. 1.3 segundos."*
> 4. *"Criando arquivo: hello_stream_test.py"*
> 5. *"Arquivo criado."*
> 6. *"Executando comando: python hello_stream_test.py"*
> 7. *"Hello, World! 0.1 segundos."*
> 8. *"Tarefa concluída com sucesso em 18 segundos."*
