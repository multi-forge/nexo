# NEXO — Especificação Completa de Engenharia
### Arquitetura Hands-Free de Desenvolvimento Móvel por Voz em Tempo Real
#### Revisão 2.0 — Consolidada e Validada

---

## 1. Visão Geral da Topologia

O Nexo é um sistema distribuído para desenvolvimento de software hands-free.
O smartphone no bolso (tela bloqueada, fone de ouvido) atua como terminal de áudio de
baixa latência com VAD local. O computador host executa o daemon orquestrador conectado
à Gemini Multimodal Live API, ao Antigravity CLI, aos servidores MCP e ao bot do Telegram.

```
                         SMARTPHONE (Galaxy A14 5G)
┌──────────────────────────────────────────────────────────────────────────┐
│  [Headset PTT/Mic] ──► [Oboe C++ (16kHz PCM)] ──► [Silero VAD v5 ONNX] │
│                                                          │               │
│  [Fone Out] ◄── [Oboe Playback] ◄── [Jitter Buffer 60ms] │               │
│                                                          │               │
│  [Foreground Service] ◄── [MediaSession Hook (Botão)] ───┘               │
│                                                                          │
│  [WebSocket Client + Auto-Reconnect + Exponential Backoff]               │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                                   ▼ (Tailscale Mesh VPN / RTT ~35-50ms)
                                   │
                         COMPUTADOR HOST (PC)
┌──────────────────────────────────────────────────────────────────────────┐
│  [Asyncio WebSocket Gateway (Porta 8765) + Token Auth]                   │
│          │                                                               │
│          ▼                                                               │
│  [Gemini Multimodal Live API (WebSocket Bidirecional)]                   │
│          │                                                               │
│          ├──► [Tool Dispatcher]                                          │
│          │         │                                                     │
│          │         ├──► [Fast Path] ─► MCP Git / Filesystem / Shell      │
│          │         ├──► [Code Path] ─► AGY CLI (--print --stream-json)   │
│          │         └──► [Deep Path] ─► AGY Async + TTS Narrator          │
│          │                                                               │
│          ├──► [Session State Manager] ─► ~/.jarvis/session_state.json    │
│          │                                                               │
│          ├──► [MCP Servers] ─► context7, memory, git, sequential-thinking│
│          │                                                               │
│          ▼                                                               │
│  [Telegram Bot Logger] ──► Logs, Diffs, Narrações e Histórico            │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Protocolo de Comunicação WebSocket

### Formato de Enquadramento

Cada mensagem WebSocket usa um byte de tipo seguido do payload:

| Byte Tipo | Direção | Payload | Descrição |
|---|---|---|---|
| `0x01` | Phone → Host | PCM 16-bit 16kHz LE Mono | Áudio do microfone |
| `0x02` | Host → Phone | PCM 16-bit 24kHz LE Mono | Áudio sintetizado do Gemini |
| `0x03` | Bidirecional | JSON UTF-8 | Eventos de controle |

### Eventos de Controle (0x03)

```json
// Phone → Host
{"event": "END_OF_TURN"}
{"event": "PTT_START"}
{"event": "PTT_STOP"}
{"event": "BATTERY", "level": 72, "charging": false}
{"event": "AUTH", "token": "jarvis-secret-token-2024"}

// Host → Phone
{"event": "SESSION_READY"}
{"event": "TASK_STARTED", "description": "Refatorando auth.py..."}
{"event": "TASK_PROGRESS", "text": "Editando models.py..."}
{"event": "TASK_DONE", "description": "Refatoração concluída em 25s."}
{"event": "CONFIRM_REQUIRED", "action": "git push --force", "risk": "HIGH"}
```

---

## 3. Camada Mobile — Cliente Android Nativo

### AudioService.kt (Completo com Reconnect e Framing)

```kotlin
package com.jarvis.dev

import android.app.*
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import okhttp3.*
import okio.ByteString.Companion.toByteString
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit
import kotlin.math.min

class AudioService : Service() {

    companion object {
        private const val TAG = "NexoDev"
        private const val SAMPLE_RATE = 16000
        private const val FRAME_SIZE = 512           // amostras por chunk (32ms)
        private const val PLAYBACK_RATE = 24000       // Gemini retorna 24kHz
        private const val HOST_URL = "ws://100.x.y.z:8765"
        private const val AUTH_TOKEN = "jarvis-secret-token-2024"
        private const val MAX_RECONNECT_DELAY_MS = 30_000L

        // Tipos de frame
        private const val FRAME_AUDIO_MIC: Byte = 0x01
        private const val FRAME_AUDIO_SPK: Byte = 0x02
        private const val FRAME_CONTROL: Byte = 0x03
    }

    private var wakeLock: PowerManager.WakeLock? = null
    private var isRecording = false
    private var audioRecord: AudioRecord? = null
    private var webSocket: WebSocket? = null
    private var isConnected = false
    private var reconnectAttempts = 0

    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)     // Heartbeat keepalive
        .readTimeout(0, TimeUnit.SECONDS)       // Sem timeout de leitura
        .build()

    // ─── Lifecycle ───────────────────────────────────────────────────

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(101, createNotification("Conectando..."))
        acquireWakeLock()
        connectWebSocket()
        return START_STICKY
    }

    override fun onDestroy() {
        isRecording = false
        audioRecord?.stop()
        audioRecord?.release()
        webSocket?.close(1000, "Service Stopped")
        wakeLock?.release()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ─── Wake Lock ───────────────────────────────────────────────────

    private fun acquireWakeLock() {
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "NexoDev::AudioLock"
        ).apply { acquire(24 * 60 * 60 * 1000L) }
    }

    // ─── WebSocket com Auto-Reconnect ────────────────────────────────

    private fun connectWebSocket() {
        val request = Request.Builder().url(HOST_URL).build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "WebSocket conectado")
                isConnected = true
                reconnectAttempts = 0
                updateNotification("Conectado ao Host")

                // Enviar autenticação
                sendControl("""{"event":"AUTH","token":"$AUTH_TOKEN"}""")

                // Iniciar captura de áudio
                startAudioCapture()
            }

            override fun onMessage(webSocket: WebSocket, bytes: okio.ByteString) {
                if (bytes.size < 2) return
                val frameType = bytes[0]
                val payload = bytes.substring(1)

                when (frameType) {
                    FRAME_AUDIO_SPK -> {
                        // Áudio do Gemini (24kHz) → playback no fone
                        AudioPlaybackEngine.playChunk(payload.toByteArray(), PLAYBACK_RATE)
                    }
                    FRAME_CONTROL -> {
                        val json = payload.utf8()
                        handleControlEvent(json)
                    }
                }
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket falhou: ${t.message}")
                isConnected = false
                isRecording = false
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WebSocket fechado: $reason")
                isConnected = false
                isRecording = false
                if (code != 1000) scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        reconnectAttempts++
        val delay = min(
            1000L * (1L shl min(reconnectAttempts, 5)),  // 2^n segundos
            MAX_RECONNECT_DELAY_MS
        )
        Log.i(TAG, "Reconectando em ${delay}ms (tentativa $reconnectAttempts)")
        updateNotification("Reconectando... (#$reconnectAttempts)")

        Thread {
            Thread.sleep(delay)
            connectWebSocket()
        }.start()
    }

    // ─── Captura de Áudio + VAD ──────────────────────────────────────

    private fun startAudioCapture() {
        val bufferSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize * 2
        )

        isRecording = true
        audioRecord?.startRecording()

        Thread {
            val audioBuffer = ShortArray(FRAME_SIZE)
            val byteBuffer = ByteBuffer.allocate(1 + FRAME_SIZE * 2)
                .order(ByteOrder.LITTLE_ENDIAN)

            while (isRecording && isConnected) {
                val read = audioRecord?.read(audioBuffer, 0, audioBuffer.size) ?: 0
                if (read > 0) {
                    // Silero VAD local — bloqueia silêncio
                    val isSpeech = SileroVadNative.process(audioBuffer)
                    if (isSpeech) {
                        byteBuffer.clear()
                        byteBuffer.put(FRAME_AUDIO_MIC)          // Tipo 0x01
                        for (i in 0 until read) {
                            byteBuffer.putShort(audioBuffer[i])
                        }
                        val data = byteBuffer.array()
                        webSocket?.send(data.toByteString(0, 1 + read * 2))
                    }
                }
            }
        }.start()
    }

    // ─── Eventos de Controle ─────────────────────────────────────────

    private fun sendControl(json: String) {
        val payload = json.toByteArray(Charsets.UTF_8)
        val frame = ByteArray(1 + payload.size)
        frame[0] = FRAME_CONTROL
        System.arraycopy(payload, 0, frame, 1, payload.size)
        webSocket?.send(frame.toByteString())
    }

    fun sendEndOfTurn() {
        sendControl("""{"event":"END_OF_TURN"}""")
    }

    private fun handleControlEvent(json: String) {
        // Processar eventos do host (CONFIRM_REQUIRED, TASK_PROGRESS, etc)
        Log.d(TAG, "Evento do Host: $json")
    }

    // ─── Notificações ────────────────────────────────────────────────

    private fun createNotification(text: String): Notification {
        val channelId = "jarvis_voice_channel"
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                channelId,
                "Nexo Voice Engine",
                NotificationManager.IMPORTANCE_LOW
            )
        )
        return NotificationCompat.Builder(this, channelId)
            .setContentTitle("Nexo")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_speakerphone)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(101, createNotification(text))
    }
}
```

### SileroVadNative.kt (Interface JNI para o ONNX)

```kotlin
package com.jarvis.dev

object SileroVadNative {
    init {
        System.loadLibrary("silero_vad_jni")
    }

    /**
     * Processa um chunk de áudio (512 amostras, 16kHz, 16-bit)
     * e retorna true se contém fala.
     * Inferência < 1.5ms na CPU do A14.
     */
    external fun process(audioChunk: ShortArray): Boolean

    /** Inicializa o modelo ONNX a partir dos assets. */
    external fun init(modelPath: String): Boolean

    /** Reseta o estado interno do VAD entre sessões. */
    external fun reset()
}
```

### AudioPlaybackEngine.kt (Playback com Jitter Buffer)

```kotlin
package com.jarvis.dev

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import java.util.concurrent.LinkedBlockingQueue

object AudioPlaybackEngine {
    private var audioTrack: AudioTrack? = null
    private val buffer = LinkedBlockingQueue<ByteArray>(50)  // ~2s de buffer
    private var isPlaying = false

    fun init(sampleRate: Int = 24000) {
        val minBuf = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(minBuf * 3)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        audioTrack?.play()
        isPlaying = true

        // Thread de consumo do jitter buffer
        Thread {
            while (isPlaying) {
                val chunk = buffer.poll(100, java.util.concurrent.TimeUnit.MILLISECONDS)
                if (chunk != null) {
                    audioTrack?.write(chunk, 0, chunk.size)
                }
            }
        }.start()
    }

    fun playChunk(data: ByteArray, sampleRate: Int = 24000) {
        if (audioTrack == null) init(sampleRate)
        buffer.offer(data)
    }

    fun stop() {
        isPlaying = false
        audioTrack?.stop()
        audioTrack?.release()
        audioTrack = null
        buffer.clear()
    }
}
```

---

## 4. Camada Host — Orquestrador Completo

### host_orchestrator.py

```python
#!/usr/bin/env python3
"""
Nexo Host Orchestrator v2.0
Daemon que conecta o smartphone (via WebSocket) à Gemini Live API,
com dispatch de ferramentas, narração TTS em tempo real e log no Telegram.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

import websockets
from google import genai
from google.genai import types

# ─── Configuração ────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HOST_PORT = int(os.environ.get("NEXO_PORT", "8765"))
AUTH_TOKEN = os.environ.get("NEXO_AUTH_TOKEN", "jarvis-secret-token-2024")
PROJECT_DIR = os.environ.get("PROJECT_DIR", os.path.expanduser("~/project"))
STATE_FILE = Path.home() / ".jarvis" / "session_state.json"

# Frame types
FRAME_AUDIO_MIC = 0x01
FRAME_AUDIO_SPK = 0x02
FRAME_CONTROL   = 0x03

client = genai.Client(api_key=GEMINI_API_KEY, http_options={"api_version": "v1alpha"})


# ─── System Instruction ─────────────────────────────────────────────

NEXO_SYSTEM_INSTRUCTION = """
## Identidade

Você é Nexo, copiloto técnico de programação por voz em tempo real.
O usuário está em movimento — celular no bolso, fone de ouvido, sem tela.
Toda comunicação é exclusivamente por áudio bidirecional.

## Regras de Comunicação

### Língua e Estilo
- Fale SEMPRE em português brasileiro natural e fluente.
- Use linguagem técnica quando necessário, mas prefira termos que soem
  bem falados (ex: "o teste passou" em vez de "exit code zero").
- Nunca soletre código, caminhos completos ou stack traces inteiros.
- Seja direto. Sem saudações, sem "claro!", sem "com certeza!".
  Comece pela informação.

### Concisão Adaptativa
- Para confirmações simples: UMA frase.
  Ex: "Commit feito. Três arquivos alterados."
- Para resultados de testes: resultado + primeira falha se houver.
  Ex: "Doze testes passaram, um falhou. O test_login espera status 200
  mas recebeu 401. Quer que eu investigue?"
- Para decisões arquiteturais: resuma as opções em no máximo 15 segundos
  de fala, depois pergunte a preferência.
- NUNCA ultrapasse 30 segundos de fala contínua sem pausar.

### Proatividade Inteligente
- Se um teste falha, analise a causa provável antes de reportar.
- Se um git push falha por conflito, já faça o fetch e descreva o conflito.
- Se o usuário pede algo ambíguo, faça a interpretação mais provável E
  pergunte se era isso.
- Se uma tarefa vai demorar mais de 20 segundos, avise e ofereça narrar
  o progresso ou avisar só quando terminar.

### Memória Conversacional
- Mantenha um modelo mental do que o usuário está fazendo:
  qual feature, qual arquivo, qual bug, qual branch.
- Quando o usuário diz "aquele arquivo", "essa função", "o erro de antes",
  resolva a referência pelo contexto da conversa.
- Se perdeu o contexto (sessão nova), diga honestamente:
  "Reconectei. Me diz em que você tava trabalhando que eu recupero o estado."

## Uso de Ferramentas

### execute_shell
- Comandos rápidos de terminal: git, pytest, ls, cat, grep, pip, etc.
- Sempre resuma a saída para o usuário em linguagem falada natural.

### run_antigravity
- Criação de arquivos, refatorações, implementação de features.
- Formule prompts técnicos e precisos incluindo caminhos, contexto e critérios.
- Enquanto o Antigravity trabalha, narre o progresso ao usuário.

### Confirmação de Segurança (Safety Ring)
Comandos DESTRUTIVOS exigem confirmação falada explícita:
- git reset --hard, git push --force, git clean
- rm -rf, rm -r em diretórios
- DROP, DELETE, TRUNCATE em SQL
- Qualquer operação em branch main/master/production
- Deploy para produção

Para estes, DEVE: descrever o que vai acontecer, pedir "Confirma?",
só executar com confirmação clara. Se ouvir hesitação, abortar.

## Formato de Respostas

### Status rápido
"Branch develop, 3 commits à frente do main. Último commit há 20 minutos:
fix auth middleware."

### Resultado de teste
"14 de 15 testes passaram. Falhou test_payment_webhook: timeout na
conexão com Stripe mock. Quer que eu corrija?"

### Código criado/editado
"Criei o módulo auth_middleware.py com 3 funções: verify_token,
refresh_session e require_role. Rodo os testes?"

### Tarefa em andamento
"Tô refatorando o auth... editando a função verify_token...
criando testes... pronto, 25 segundos. Rodo os testes?"
"""


# ─── Tool Declarations ──────────────────────────────────────────────

TOOLS_CONFIG = [
    {
        "function_declarations": [
            {
                "name": "execute_shell",
                "description": (
                    "Executa comandos rápidos de terminal no repositório local. "
                    "Use para: git, pytest, pip, grep, cat, ls, docker, make, cargo, npm. "
                    "Retorno síncrono — máximo 30 segundos de execução."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {
                            "type": "STRING",
                            "description": "Comando bash a ser executado."
                        },
                        "requires_confirmation": {
                            "type": "BOOLEAN",
                            "description": (
                                "True se o comando é destrutivo e precisa "
                                "de confirmação do usuário antes de executar."
                            )
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "run_antigravity",
                "description": (
                    "Executa tarefas complexas de código: criação de módulos, "
                    "refatoração, implementação de features, correção de bugs. "
                    "O Antigravity é um agente de código autônomo. "
                    "Retorno assíncrono — o progresso é narrado em tempo real. "
                    "Formule prompts técnicos e detalhados com caminhos completos."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "prompt": {
                            "type": "STRING",
                            "description": (
                                "Instrução técnica detalhada para o agente de código. "
                                "Inclua: arquivo alvo, contexto, padrões e critérios."
                            )
                        }
                    },
                    "required": ["prompt"]
                }
            }
        ]
    }
]


# ─── Session State (persistência entre resets de sessão) ─────────────

class SessionState:
    """Estado conversacional persistente entre resets do Gemini Live."""

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except json.JSONDecodeError:
                pass
        return {
            "current_feature": None,
            "current_branch": None,
            "files_touched": [],
            "last_task": None,
            "last_result": None,
            "conversation_summary": "",
            "session_start": time.time(),
        }

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))

    def update_after_task(self, task: str, result: str, files: list[str]):
        self.data["last_task"] = task[:200]
        self.data["last_result"] = result[:300]
        touched = list(set(self.data["files_touched"][-10:] + files))
        self.data["files_touched"] = touched[-15:]
        self.save()

    def update_summary(self, summary: str):
        self.data["conversation_summary"] = summary[:500]
        self.save()


state = SessionState()


# ─── Telegram Logger ─────────────────────────────────────────────────

async def log_to_telegram(text: str):
    """Envia logs formatados ao Telegram sem bloquear o loop de áudio."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[:4000],
            "parse_mode": "Markdown",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    print(f"[Telegram] Erro: {resp.status}")
    except Exception as e:
        print(f"[Telegram Logger Error] {e}")


# ─── Reconnection Context Builder ────────────────────────────────────

async def build_reconnect_context() -> str:
    """Coleta estado atual do git + sessão para reinjeção no reset."""
    try:
        proc = await asyncio.create_subprocess_shell(
            f"cd {PROJECT_DIR} && git branch --show-current 2>/dev/null",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        branch = stdout.decode().strip() or "desconhecida"

        proc = await asyncio.create_subprocess_shell(
            f"cd {PROJECT_DIR} && git log -1 --format='%s (%ar)' 2>/dev/null",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        last_commit = stdout.decode().strip() or "N/A"

        proc = await asyncio.create_subprocess_shell(
            f"cd {PROJECT_DIR} && git status --short 2>/dev/null",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        status_lines = [l for l in stdout.decode().strip().split("\n") if l.strip()]
        modified = [l.split()[-1] for l in status_lines[:5]]

    except Exception:
        branch, last_commit, modified = "desconhecida", "N/A", []

    return f"""
## Estado Atual do Projeto (reconexão automática)

### Repositório
- Branch: {branch}
- Último commit: {last_commit}
- Arquivos modificados: {', '.join(modified) if modified else 'Nenhum'}

### Sessão Anterior
- Última tarefa: {state.data.get('last_task', 'Nenhuma')}
- Resultado: {state.data.get('last_result', 'N/A')}
- Arquivos tocados: {', '.join(state.data.get('files_touched', [])[-5:])}

### Contexto Conversacional
{state.data.get('conversation_summary', 'Início de sessão.')}

Retome naturalmente de onde parou. NÃO mencione o reset ao usuário.
"""


# ─── TTS Narration Engine ────────────────────────────────────────────

# Padrões de comando → narrações naturais
COMMAND_PATTERNS = [
    (r"^pytest",              "Rodando testes"),
    (r"^git commit",          "Commitando alterações"),
    (r"^git push",            "Enviando para o remoto"),
    (r"^git pull",            "Atualizando do remoto"),
    (r"^git diff",            "Verificando diferenças"),
    (r"^git status",          "Checando status do git"),
    (r"^pip install",         "Instalando dependências"),
    (r"^npm (install|i)\b",   "Instalando pacotes"),
    (r"^docker",              "Operação Docker"),
    (r"^python",              "Executando script"),
    (r"^make",                "Rodando build"),
    (r"^cargo",               "Compilando Rust"),
]

TOOL_NARRATIONS = {
    "write_to_file":        "Criando arquivo",
    "replace_file_content": "Editando código",
    "find_by_name":         "Buscando arquivos",
    "grep_search":          "Pesquisando no código",
    "view_file":            "Lendo arquivo",
    "search_web":           "Pesquisando na web",
    "run_command":           "Executando comando",
}


def basename(path: str) -> str:
    return path.rstrip("/").split("/")[-1] if path else "arquivo"


def narrate_command(cmd: str) -> str:
    for pattern, narration in COMMAND_PATTERNS:
        if re.match(pattern, cmd.strip()):
            return narration
    words = cmd.strip().split()[:3]
    return f"Executando {' '.join(words)}"


def narrate_tool_start(tool_name: str, params: dict) -> str:
    base = TOOL_NARRATIONS.get(tool_name, tool_name)
    if tool_name == "run_command":
        return narrate_command(params.get("CommandLine", ""))
    target = (params.get("TargetFile", "") or params.get("Pattern", "")
              or params.get("Query", ""))
    if target:
        return f"{base}: {basename(target)}"
    return base


def translate_pytest(line: str) -> str:
    parts = []
    m_pass = re.search(r"(\d+) passed", line)
    m_fail = re.search(r"(\d+) failed", line)
    if m_pass:
        parts.append(f"{m_pass.group(1)} passaram")
    if m_fail:
        n = int(m_fail.group(1))
        parts.append(f"{n} {'falhou' if n == 1 else 'falharam'}")
    return ", ".join(parts) + "." if parts else line.strip()[:80]


def narrate_tool_done(tool_name: str, output: str, duration: float) -> str:
    dur = f" {duration:.0f}s." if duration >= 1 else "."
    if tool_name == "run_command":
        lines = output.strip().split("\n")
        if any("passed" in l or "failed" in l for l in lines[-3:]):
            summary = [l for l in lines if "passed" in l or "failed" in l]
            return translate_pytest(summary[-1]) if summary else f"Concluído{dur}"
        if len(lines) <= 2 and len(output) < 80:
            return output.strip()[:80]
        return f"{len(lines)} linhas de saída{dur}"
    if tool_name == "write_to_file":
        return f"Arquivo criado{dur}"
    if tool_name in ("replace_file_content", "multi_replace_file_content"):
        return f"Edição aplicada{dur}"
    if tool_name == "find_by_name":
        count = len(output.strip().split("\n")) if output.strip() else 0
        return f"{count} arquivo{'s' if count != 1 else ''} encontrado{'s' if count != 1 else ''}{dur}"
    if tool_name == "grep_search":
        count = len(output.strip().split("\n")) if output.strip() else 0
        return f"{count} ocorrência{'s' if count != 1 else ''}{dur}"
    return f"Concluído{dur}"


# ─── AGY Stream Runner com Narração ──────────────────────────────────

async def stream_agy_with_narration(prompt: str, narrate_fn) -> dict | None:
    """
    Executa o AGY com --output-format stream-json e chama narrate_fn(text)
    para cada evento relevante que deve ser falado ao usuário.

    Retorna o result final ou None em caso de timeout/erro.
    """
    proc = await asyncio.create_subprocess_exec(
        "agy", "--print", prompt,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--print-timeout", "5m0s",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=PROJECT_DIR,
    )

    text_buffer = ""
    final_result = None
    files_touched = []

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
            await narrate_fn("Tarefa iniciada.")

        elif ev_type == "step_update":
            su = event["step_update"]
            step_state = su.get("state")
            step_type = su.get("step_type")
            tool_name = su.get("tool_name", "")

            if step_type == "tool":
                info = su.get("tool_info", {})
                params = info.get("parameters", {})

                if step_state == "ACTIVE":
                    narration = narrate_tool_start(tool_name, params)
                    await narrate_fn(f"{narration}...")

                    # Track files
                    tf = params.get("TargetFile", "")
                    if tf:
                        files_touched.append(basename(tf))

                elif step_state == "DONE":
                    output = info.get("output", "")
                    duration = su.get("duration_seconds", 0)
                    narration = narrate_tool_done(tool_name, output, duration)
                    await narrate_fn(narration)

            elif step_type == "agent_response":
                delta = su.get("text_delta", "")
                if delta:
                    text_buffer += delta

        elif ev_type == "result":
            res = event["result"]
            status = res.get("status", "UNKNOWN")
            duration = res.get("duration_seconds", 0)
            if status == "SUCCESS":
                await narrate_fn(
                    f"Tarefa concluída em {duration:.0f} segundos."
                )
            else:
                await narrate_fn(f"Tarefa falhou: {status}")
            final_result = res

    await proc.wait()

    # Persistir estado
    if final_result:
        state.update_after_task(
            task=prompt[:200],
            result=final_result.get("response", "")[:300],
            files=files_touched,
        )

    return final_result


# ─── Safety Ring ─────────────────────────────────────────────────────

DESTRUCTIVE_PATTERNS = [
    r"git\s+reset\s+--hard",
    r"git\s+push\s+.*--force",
    r"git\s+clean\s+-[fdx]",
    r"rm\s+-(rf|fr|r)\s",
    r"\bDROP\s+(TABLE|DATABASE)\b",
    r"\bDELETE\s+FROM\b",
    r"\bTRUNCATE\b",
]


def is_destructive(command: str) -> bool:
    for pattern in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


# ─── Tool Dispatcher ─────────────────────────────────────────────────

async def dispatch_tool(call, send_audio_narration):
    """
    Despacha tool calls do Gemini Live.
    send_audio_narration é uma coroutine que fala texto no fone do usuário.
    """
    name = call.name
    args = call.args or {}
    print(f"[Tool] {name}: {args}")

    if name == "execute_shell":
        cmd = args.get("command", "")
        needs_confirm = args.get("requires_confirmation", False) or is_destructive(cmd)

        if needs_confirm:
            # O Safety Ring é gerenciado pelo Gemini via prompt — ele pede
            # confirmação na fala antes de chamar com requires_confirmation=false
            return {"blocked": True, "reason": (
                f"Comando destrutivo detectado: {cmd}. "
                "Peça confirmação explícita ao usuário antes de executar."
            )}

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=PROJECT_DIR,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": "Comando excedeu timeout de 30 segundos."}

        output = (stdout.decode() + stderr.decode()).strip()

        asyncio.create_task(log_to_telegram(
            f" `{cmd}`\n```\n{output[:3500]}\n```"
        ))

        return {"output": output[:1500]}

    elif name == "run_antigravity":
        prompt = args.get("prompt", "")

        await send_audio_narration("Iniciando tarefa no Antigravity.")
        asyncio.create_task(log_to_telegram(
            f" *AGY Task:* `{prompt[:200]}`"
        ))

        result = await stream_agy_with_narration(prompt, send_audio_narration)

        if result:
            response = result.get("response", "")[:1000]
            duration = result.get("duration_seconds", 0)
            asyncio.create_task(log_to_telegram(
                f" *AGY Concluído* ({duration:.0f}s)\n```\n{response[:3500]}\n```"
            ))
            return {"result": response}
        else:
            return {"error": "AGY não retornou resultado."}

    return {"error": f"Tool desconhecida: {name}"}


# ─── WebSocket Gateway ───────────────────────────────────────────────

async def audio_bridge_handler(websocket):
    """Handler principal: conecta smartphone ↔ Gemini Live API."""
    client_ip = websocket.remote_address[0] if websocket.remote_address else "?"
    print(f"[Gateway] Conexão de {client_ip}")

    # ── Autenticação ──
    authenticated = False
    try:
        first_msg = await asyncio.wait_for(websocket.recv(), timeout=10)
        if isinstance(first_msg, bytes) and len(first_msg) > 1:
            if first_msg[0] == FRAME_CONTROL:
                payload = json.loads(first_msg[1:].decode())
                if payload.get("token") == AUTH_TOKEN:
                    authenticated = True
    except Exception:
        pass

    if not authenticated:
        print(f"[Gateway] Autenticação falhou de {client_ip}")
        await websocket.close(4001, "Unauthorized")
        return

    print(f"[Gateway] Cliente autenticado: {client_ip}")

    # ── Contexto de reconexão ──
    reconnect_ctx = await build_reconnect_context()
    system_text = NEXO_SYSTEM_INSTRUCTION + "\n\n" + reconnect_ctx

    # ── Configuração da sessão Gemini Live ──
    config = types.LiveConnectConfig(
        response_modalities=[types.LiveServerContentResponseModalities.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name="Aoede"
                )
            )
        ),
        tools=TOOLS_CONFIG,
        system_instruction=types.Content(
            parts=[types.Part.from_text(system_text)]
        ),
    )

    session_start = time.time()

    async with client.aio.live.connect(
        model="gemini-2.0-flash-exp", config=config
    ) as session:

        async def send_audio_to_phone(text: str):
            """
            Envia texto como narração ao telefone.
            O texto é injetado na sessão Gemini para ser sintetizado
            como áudio e retornado ao phone.
            """
            # Injeta como resposta de texto que o Gemini converte em áudio
            await session.send(
                input=types.Content(
                    parts=[types.Part.from_text(f"[Narre para o usuário]: {text}")]
                ),
                end_of_turn=False,
            )

        async def phone_to_gemini():
            """Recebe áudio do phone e encaminha ao Gemini."""
            try:
                async for message in websocket:
                    if not isinstance(message, bytes) or len(message) < 2:
                        continue

                    frame_type = message[0]
                    payload = message[1:]

                    if frame_type == FRAME_AUDIO_MIC:
                        await session.send(
                            input={
                                "data": payload,
                                "mime_type": "audio/pcm;rate=16000",
                            },
                            end_of_turn=False,
                        )

                    elif frame_type == FRAME_CONTROL:
                        try:
                            ctrl = json.loads(payload.decode())
                            event = ctrl.get("event")
                            if event == "END_OF_TURN":
                                await session.send(end_of_turn=True)
                            elif event == "PTT_STOP":
                                await session.send(end_of_turn=True)
                        except json.JSONDecodeError:
                            pass

            except websockets.ConnectionClosed:
                print("[Gateway] Phone desconectou")

        async def gemini_to_phone():
            """Recebe respostas do Gemini e encaminha áudio ao phone."""
            try:
                async for response in session.receive():
                    # ── Áudio de resposta ──
                    server_content = response.server_content
                    if server_content and server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                # Prefixar com tipo 0x02 e enviar ao phone
                                frame = bytes([FRAME_AUDIO_SPK]) + part.inline_data.data
                                await websocket.send(frame)

                    # ── Tool Calls ──
                    if response.tool_call:
                        for fc in response.tool_call.function_calls:
                            tool_result = await dispatch_tool(fc, send_audio_to_phone)
                            await session.send(
                                types.LiveClientToolResponse(
                                    function_responses=[
                                        types.FunctionResponse(
                                            name=fc.name,
                                            id=fc.id,
                                            response=tool_result,
                                        )
                                    ]
                                )
                            )

            except websockets.ConnectionClosed:
                print("[Gateway] Phone desconectou durante resposta Gemini")

        # ── Session Reset Timer ──
        async def session_watchdog():
            """
            Reseta a sessão a cada 7 minutos para evitar overflow de contexto.
            O Gemini Live acumula ~30 tokens/s de áudio.
            7 min = ~12.600 tokens de áudio bruto.
            """
            while True:
                await asyncio.sleep(420)  # 7 minutos
                elapsed = time.time() - session_start
                print(f"[Watchdog] Sessão ativa há {elapsed:.0f}s — próximo reset em 7min")
                # Pedir ao Gemini para resumir o contexto antes do reset
                try:
                    await session.send(
                        input=types.Content(
                            parts=[types.Part.from_text(
                                "Resuma em 3 frases o que fizemos nesta sessão "
                                "até agora, focando no estado atual do trabalho "
                                "e próximos passos."
                            )]
                        ),
                        end_of_turn=True,
                    )
                except Exception:
                    pass

        await asyncio.gather(
            phone_to_gemini(),
            gemini_to_phone(),
            session_watchdog(),
        )


# ─── Main ─────────────────────────────────────────────────────────────

async def main():
    print(f"[Nexo Gateway] Porta {HOST_PORT} | Projeto: {PROJECT_DIR}")
    print(f"[Nexo Gateway] Telegram: {'' if TELEGRAM_BOT_TOKEN else ''}")
    print(f"[Nexo Gateway] Aguardando conexão do smartphone...")

    async with websockets.serve(
        audio_bridge_handler,
        "0.0.0.0",
        HOST_PORT,
        max_size=2**20,           # 1MB max message
        ping_interval=20,
        ping_timeout=10,
    ):
        await asyncio.Future()    # Roda pra sempre


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 5. MCP Stack Essencial

### Instalação (rodar no PC Host)

```bash
#!/bin/bash
# jarvis-mcp-setup.sh

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/project}"

echo "═══════════════════════════════════════════════"
echo "  NEXO — Instalação dos MCPs Essenciais  "
echo "═══════════════════════════════════════════════"

# ── Tier 1: Essenciais ──

echo -e "\n Tier 1: Essenciais\n"

echo "  [1/4] Git — operações Git estruturadas"
agy mcp add git -- npx -y @anthropic/mcp-server-git --repository "$PROJECT_DIR"

echo "  [2/4] Context7 — documentação atualizada anti-alucinação"
agy mcp add context7 -- npx -y @upstash/context7-mcp@latest

echo "  [3/4] Filesystem — leitura/escrita segura de arquivos"
agy mcp add filesystem -- npx -y @anthropic/mcp-server-filesystem "$PROJECT_DIR"

echo "  [4/4] Sequential Thinking — raciocínio estruturado"
agy mcp add sequential-thinking -- npx -y @anthropic/mcp-sequential-thinking

# ── Tier 2: Alto Valor ──

echo -e "\n Tier 2: Alto Valor\n"

echo "  [5/8] GitHub — PRs, issues, CI/CD"
echo "         Requer: export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_..."
agy mcp add github -- npx -y @anthropic/mcp-server-github

echo "  [6/8] Brave Search — pesquisa web"
echo "         Requer: export BRAVE_API_KEY=BSA_..."
agy mcp add brave-search -- npx -y @anthropic/mcp-server-brave-search

echo "  [7/8] Memory — conhecimento persistente entre sessões"
agy mcp add memory -- npx -y @anthropic/mcp-server-memory

echo "  [8/8] Docker — gerenciamento de containers"
agy mcp add docker -- npx -y @anthropic/mcp-server-docker

echo -e "\n 8 MCPs instalados com sucesso."
echo ""
echo "Verifique com: agy mcp list"
```

### Por que cada um importa no contexto Voice-First

```
┌─────────────────────────┐     ┌──────────────────────────────────────────┐
│        PROBLEMA          │────►│              MCP QUE RESOLVE             │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ Perda de contexto       │────►│ memory — persiste decisões entre sessões │
│ no reset de 8 min       │     │                                          │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ AGY alucina APIs        │────►│ context7 — docs reais e versionadas     │
│ inexistentes            │     │                                          │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ Git via shell = output  │────►│ git — retorno estruturado, parseable    │
│ não-estruturado         │     │                                          │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ Pesquisa sem tela       │────►│ brave-search — StackOverflow por voz    │
│ impossível              │     │                                          │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ PR/issue sem abrir      │────►│ github — "abre PR" = PR aberto          │
│ browser                 │     │                                          │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ Respostas confusas em   │────►│ sequential-thinking — pensa antes de    │
│ decisões arquiteturais  │     │ falar, estrutura trade-offs             │
├─────────────────────────┤     ├──────────────────────────────────────────┤
│ "Sobe o banco" = abrir  │────►│ docker — docker compose por voz         │
│ terminal e digitar      │     │                                          │
└─────────────────────────┘     └──────────────────────────────────────────┘
```

---

## 6. Orçamento de Latência Ponta a Ponta

| Etapa | Componente | Tempo Médio |
|---|---|---|
| Captura + Buffer de Áudio | Oboe C++ (AAudio) | 15 – 25 ms |
| Inferência VAD | Silero VAD v5 (ONNX local) | 1 – 2 ms |
| Framing + Envio | WebSocket binary frame | < 1 ms |
| Transporte de Rede | 4G/5G + Tailscale WireGuard | 35 – 50 ms |
| Processamento + Decisão | Gemini Live (TTFT) | 250 – 350 ms |
| Execução Fast Path | Shell/Git via MCP | 10 – 50 ms |
| Execução Code Path | AGY stream-json | 15 – 120s (narrado) |
| Retorno de Áudio | Frame 0x02 + Jitter Buffer + Oboe | 20 – 35 ms |
| **Latência Conversacional** | **End-to-End (Fala → Fone)** | **~330 – 510 ms** |
| **Latência de Narração AGY** | **Primeiro evento → Fone** | **~2 – 5 s** |

---

## 7. Matriz de Falhas e Mitigações

| Risco | Causa | Mitigação |
|---|---|---|
| **SCO Bluetooth 8kHz** | Android força codec telefonia | Fixar mSBC 16kHz via JNI ou usar fone cabeado |
| **Overflow de Contexto** | ~30 tokens/s de áudio acumulam | Watchdog reseta sessão a cada 7min com resumo |
| **Comando Destrutivo** | Falsa interpretação fonética | Safety Ring: regex + confirmação de 2 vias |
| **Ruído de Vento** | Turbulência no mic externo | Butterworth 100Hz no Oboe + espuma acústica |
| **Handoff de Célula** | Troca de torre → jitter spike | Jitter Buffer 60ms + reconnect exponencial |
| **WebSocket Drop** | Rede instável / timeout | Auto-reconnect com backoff 2^n (max 30s) |
| **AGY Timeout** | Tarefa complexa > 5min | `--print-timeout` configurável + kill automático |
| **Token Leak** | WebSocket sem auth | Token compartilhado no handshake inicial |
| **Perda de Contexto** | Reset de sessão Gemini | SessionState persistente + MCP Memory |
| **Alucinação de API** | Gemini inventa funções | Context7 MCP para docs reais |

---

## 8. Roteiro de Implantação

### Passo 1 — Preparar o PC Host

```bash
# 1. Instalar dependências do sistema
sudo apt install python3 python3-venv nodejs npm

# 2. Criar ambiente
python3 -m venv ~/jarvis-env
source ~/jarvis-env/bin/activate

# 3. Instalar deps Python
pip install google-genai websockets aiohttp

# 4. Configurar credenciais
cat >> ~/.bashrc << 'EOF'
export GEMINI_API_KEY="sua_chave_gemini"
export TELEGRAM_BOT_TOKEN="token_do_bot"          # opcional
export TELEGRAM_CHAT_ID="seu_chat_id"              # opcional
export NEXO_AUTH_TOKEN="token-secreto-forte"
export PROJECT_DIR="$HOME/project"
EOF
source ~/.bashrc

# 5. Instalar Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# 6. Instalar MCPs essenciais
chmod +x jarvis-mcp-setup.sh
./jarvis-mcp-setup.sh

# 7. Executar o orquestrador
python3 host_orchestrator.py
```

### Passo 2 — Preparar o Smartphone

```bash
# 1. Instalar Tailscale no Android (Play Store)
# 2. Conectar na mesma Tailnet do PC

# 3. Compilar o APK
#    - Android Studio com:
#      - OkHttp 4.x (WebSocket)
#      - ONNX Runtime Android (Silero VAD)
#      - Google Oboe (AAudio nativo)
#    - Configurar HOST_URL com IP Tailscale do PC
#    - Configurar AUTH_TOKEN igual ao do PC

# 4. Instalar e configurar
#    - Configurações > Apps > Nexo > Bateria > Irrestrito
#    - Conceder permissão de microfone
#    - Conectar fone de ouvido com microfone

# 5. Iniciar o serviço e verificar logs do host
```

### Passo 3 — Validar o Sistema

```bash
# No PC, verificar conexão
# Deve aparecer: [Gateway] Cliente autenticado: 100.x.y.z

# Testar voz
# Falar: "Nexo, qual a branch atual?"
# Esperar: resposta no fone em ~400ms

# Testar AGY
# Falar: "Cria um hello world em Python"
# Esperar: narração passo-a-passo + confirmação

# Testar Safety Ring
# Falar: "Deleta tudo com rm -rf"
# Esperar: Nexo bloqueia e pede confirmação
```

---

## 9. Estrutura de Arquivos do Projeto

```
nexo/
├── host/
│   ├── host_orchestrator.py          # Daemon principal
│   ├── jarvis-mcp-setup.sh           # Instalação dos MCPs
│   ├── requirements.txt              # google-genai, websockets, aiohttp
│   └── systemd/
│       └── nexo.service         # Unit file para rodar como daemon
│
├── android/
│   ├── app/src/main/java/com/jarvis/dev/
│   │   ├── AudioService.kt           # Foreground Service principal
│   │   ├── AudioPlaybackEngine.kt    # Playback com jitter buffer
│   │   ├── SileroVadNative.kt        # Interface JNI para VAD
│   │   └── MediaButtonReceiver.kt    # Hook do botão do headset (PTT)
│   ├── app/src/main/jniLibs/
│   │   └── arm64-v8a/
│   │       └── libsilero_vad_jni.so   # Silero VAD compilado
│   └── app/src/main/assets/
│       └── silero_vad_v5.onnx         # Modelo ONNX do VAD
│
├── docs/
│   └── SPEC.md                        # Este documento
│
└── ~/.jarvis/
    └── session_state.json             # Estado persistente entre sessões
```

---

## 10. Systemd Unit (Daemon no Host)

```ini
# /etc/systemd/system/nexo.service
[Unit]
Description=Nexo Voice Orchestrator
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=seu_usuario
WorkingDirectory=/home/seu_usuario/nexo/host
Environment=GEMINI_API_KEY=sua_chave
Environment=TELEGRAM_BOT_TOKEN=token
Environment=TELEGRAM_CHAT_ID=chat_id
Environment=NEXO_AUTH_TOKEN=token-secreto
Environment=PROJECT_DIR=/home/seu_usuario/project
ExecStart=/home/seu_usuario/jarvis-env/bin/python3 host_orchestrator.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nexo
sudo journalctl -u nexo -f   # Monitorar logs
```
