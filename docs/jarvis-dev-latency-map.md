# JARVIS-DEV: Mapa Inteligente de Latência Extrema (Ultra-Low Latency Pipeline)

## 1. Topologia do Pipeline "Zero-Copy" de Latência Mínima

Para atingir a latência do limite da percepção humana conversacional (~300–400 ms), eliminamos todas as camadas intermediárias de abstração, serialização desnecessária e I/O de disco.

```
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│ CAMADA 1: SMARTPHONE (EDGE DSP EM C++ / OBOE EXCLUSIVE AAUDIO)                                   │
│                                                                                                   │
│  [Microfone 16kHz] ──► [DMA Hardware Buffer (16ms / 256 samples)]                                 │
│                               │                                                                   │
│                               ▼ (Zero-Copy Pointer)                                               │
│                        [Silero VAD v5 ONNX (ARM NEON FP16: 0.8ms)]                                │
│                               │                                                                   │
│                    (Se Fala detectada: Frame 0x01)                                                │
│                               │                                                                   │
│                               ▼                                                                   │
│             [Socket UDP Direto / WebTransport (WireGuard Kernel)]                                 │
└───────────────────────────────┬───────────────────────────────────────────────────────────────────┘
                                │  RTT 5G P2P Tailscale: ~25 - 35 ms
                                ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│ CAMADA 2: COMPUTADOR HOST (GATEWAY UVLOOP / ZERO-TRANSCODE BRIDGE)                                │
│                                                                                                   │
│  [Gateway Socket Assíncrono (uvloop C-Engine: 0.5ms)]                                             │
│                               │                                                                   │
│                               ▼ (Stream Pass-through sem Base64)                                  │
│  [Gemini Multimodal Live API (Bidirectional gRPC / WebSocket com Google Cloud Direct Peering)]    │
│                               │                                                                   │
│        ┌──────────────────────┴───────────────────────────────────┐                               │
│        ▼                                                          ▼                               │
│  [Decisão Conversacional]                                 [Tool Call Detectada]                   │
│        │                                                          │                               │
│        ▼                                                  [RAM Fast Path In-Process]              │
│  [Síntese Neural 24kHz]                                   • Git RAM Cache (3ms)                   │
│  (TTFT Audio: ~220-300ms)                                 • Local FS mmap (1ms)                   │
│        │                                                  • Shell Instant (<15ms)                 │
│        │                                                          │                               │
│        │                                                          ▼                               │
│        │                                          [Injeta ToolResponse no Stream: 2ms]            │
│        │                                                          │                               │
│        └──────────────────────┬───────────────────────────────────┘                               │
│                               ▼                                                                   │
│             [1º Chunk de Áudio PCM 24kHz (40ms / 1920 bytes)]                                      │
└───────────────────────────────┬───────────────────────────────────────────────────────────────────┘
                                │  Downlink Tailscale 5G: ~25 - 35 ms
                                ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│ CAMADA 3: SMARTPHONE PLAYBACK (DMA STREAMING DIRETO AO FONE)                                      │
│                                                                                                   │
│  [Recepção do 1º Chunk (Frame 0x02)]                                                              │
│               │                                                                                   │
│               ▼                                                                                   │
│  [Jitter Buffer Adaptativo C++ (30ms)] ──► [Oboe AAudio Output Stream] ──► 🎧 [Ouvido do Usuário] │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Orçamento Físico de Latência Passo a Passo

| Etapa do Pipeline | Tecnologia de Ponta Utilizada | Latência Física | Acumulado |
|---|---|---|---|
| **1. Captura Mic Hardware** | Oboe C++ em modo `AAUDIO_PERFORMANCE_MODE_LOW_LATENCY` | **16.0 ms** | 16 ms |
| **2. Inferência VAD Local** | Silero VAD v5 otimizado com ARM NEON SIMD | **0.8 ms** | 17 ms |
| **3. Empacotamento de Frame** | Prefixo de 1 byte (`0x01`) sem serialização JSON | **0.1 ms** | 17 ms |
| **4. Rede Móvel 5G / P2P** | Tailscale Mesh WireGuard P2P (UDP direto) | **28.0 ms** | 45 ms |
| **5. Roteamento no Host** | Daemon Python com motor C `uvloop` (zero-copy) | **0.6 ms** | 46 ms |
| **6. Trânsito Host → Google** | Conexão TLS mantida viva via HTTP/2 / WebSocket | **18.0 ms** | 64 ms |
| **7. Inferência Gemini Live** | Processamento neural de áudio + Time to First Token (TTFT) | **240.0 ms** | 304 ms |
| **8. Trânsito Google → Host** | 1º Chunk de áudio gerado (1920 bytes PCM 24kHz) | **18.0 ms** | 322 ms |
| **9. Repasse Host → Phone** | Transmissão binária imediata do primeiro chunk | **28.0 ms** | 350 ms |
| **10. Jitter Buffer + DMA** | Jitter Buffer dinâmico + Driver de Saída Oboe | **25.0 ms** | **~375 ms** |

> ⏱️ **Latência Conversacional Total (Fala → Áudio no Fone):** **~375 milissegundos**.
> *Nota: O limiar da conversa humana natural em tempo real é de 250ms a 400ms. O sistema opera dentro da faixa de fluidez humana.*

---

## 3. As 5 Técnicas de "Engenharia Inteligente" para Ganho Extremo

### 1. Speculative Tool Execution (Execução Especulativa)
Quando o usuário diz: *"Jarvis, qual foi o último commit na branch atual?"*
- O Host **não espera** a IA pedir a tool.
- O Host detecta o trigger no início da frase (*"último commit"*, *"status do git"*, *"branch"*) e já dispara um `git log -1` e `git branch` para a memória RAM enquanto o áudio ainda está sendo enviado ao Google.
- Quando a chamada `execute_shell` chega da Gemini Live, o resultado **já está na RAM pronto** → Latência da ferramenta: **0.1 ms**.

---

### 2. Zero-Copy Binary Framing (Sem Base64, Sem JSON no Áudio)
- Transmissão tradicional: Converte PCM binário para Base64 (overhead de 33% em banda e processamento de encode/decode).
- **Engenharia Inteligente:**
  - Phone envia: `[0x01][raw_pcm_16khz_bytes]`
  - Host recebe e injeta direto no payload da Live API sem decodificação intermediária.
  - Gemini retorna PCM binário → Host retransmite: `[0x02][raw_pcm_24khz_bytes]`.

---

### 3. Chunk Pipelining (Streaming Sem Bloqueio)
O áudio **nunca espera a frase inteira ser gerada para começar a tocar**:
- O Gemini gera a resposta em pedaços de 40ms de áudio.
- O primeiro pedaço (1920 bytes) chega ao fone e já começa a tocar enquanto a IA ainda está gerando o resto da frase.
- O Jitter Buffer de 30ms garante que o fone não engasgue mesmo com oscilações de 4G/5G.

---

### 4. Dual-Track Connection Architecture (Dois Canais Paralelos)

```
[Canal 1: Voz em Tempo Real]  ──► WebTransport / WebSocket Live API (Prioridade Máxima / Baixa Latência)
[Canal 2: Agente de Código]    ──► Subprocesso AGY Local (Deep Path assíncrono / Streaming NDJSON)
```
- Se o Antigravity levar 30 segundos escrevendo código, o canal de voz **permanece aberto a 375ms** para bater papo, tirar dúvidas ou receber ordens de cancelamento.

---

### 5. Persistent Hot-Workers (Workers Pré-Aquecidos)
- Processos Git, Shell e instâncias do AGY mantêm handles de arquivo abertos em RAM (`/dev/shm` ou RAM disk) para evitar o custo de cold-start de inicialização de processos.
