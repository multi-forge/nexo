# Nexo

**Voice-First Autonomous Engineering Operating System**

> Real-time voice orchestration of autonomous AI agents and native operating system control
> over a userspace WireGuard mesh network (Tailscale tsnet).

Nexo is a high-performance orchestration architecture that transforms a smartphone and a Windows workstation into a unified, voice-controlled engineering platform. Audio streams bidirectionally between an Android client and a Windows host daemon via a P2P encrypted tunnel, with Gemini Multimodal Live API providing real-time speech understanding, native function calling, and voice synthesis.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [System Architecture Diagram](#system-architecture-diagram)
- [Latency Budget](#latency-budget)
- [Core Components](#core-components)
  - [Host Daemon (Windows)](#host-daemon-windows)
  - [Mobile Client (Android)](#mobile-client-android)
  - [Infrastructure](#infrastructure)
- [Voice UX: Psychology-Informed Design](#voice-ux-psychology-informed-design)
  - [Latency Perception](#1-latency-perception)
  - [Silence Tolerance](#2-silence-tolerance)
  - [Cognitive Load Management](#3-cognitive-load-management)
  - [Anti-Fatigue Controls](#4-anti-fatigue-controls)
  - [Enterprise Persona](#5-enterprise-persona)
  - [Barge-in and Interruption](#6-barge-in-and-interruption)
- [Dynamic Audio Queue](#dynamic-audio-queue)
- [Security Model](#security-model)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Repository Structure](#repository-structure)
- [Academic References](#academic-references)
- [License](#license)

---

## Architecture Overview

Nexo operates as three cooperating layers:

| Layer | Component | Role |
| :--- | :--- | :--- |
| **Transport** | Tailscale (tsnet / WireGuard) | Encrypted P2P mesh, NAT traversal, zero-config VPN |
| **Orchestration** | Nexo Host Daemon | WebSocket gateway, Gemini Live relay, tool dispatch, AGY bridge |
| **Execution** | Win32 Native Engine + AGY | In-memory OS control (mouse, keyboard, windows, screen), autonomous code agent |

The mobile client captures 16kHz PCM audio from the microphone, frames it with a `0x01` prefix byte, and streams it over WebSocket to the host daemon. The daemon relays the audio to the Gemini Multimodal Live API, which performs real-time voice activity detection (VAD), speech recognition, intent resolution, and native function calling. Tool results are returned to the model, which synthesizes a 24kHz voice response streamed back to the client with a `0x02` prefix byte.

---

## System Architecture Diagram

```
                        ANDROID CLIENT
                   (Oboe C++ / 16kHz PCM)
                            |
                   [0x01] Audio frames
                   [0x03] Turn-complete
                            |
                     WireGuard Tunnel
                    (Tailscale tsnet)
                      ~130ms P2P RTT
                            |
                            v
              +─────────────────────────────+
              |     NEXO HOST DAEMON        |
              |     (Windows / Port 8765)   |
              |                             |
              |  WebSocket Gateway          |
              |       |                     |
              |       v                     |
              |  Gemini 3.1 Live API  <─────|──── System Prompt
              |  (wss:// BidiGenerate)      |     + Tool Declarations
              |       |                     |
              |       +── Tool Call ────>   |
              |       |                     |
              |  +────v──────────────+      |
              |  | Win32 Native      |      |
              |  | Engine (ctypes)   |      |
              |  | - Mouse control   |      |
              |  | - Keyboard input  |      |
              |  | - Window mgmt     |      |
              |  | - Screen capture  |      |
              |  +───────────────────+      |
              |                             |
              |  +───────────────────+      |
              |  | AGY Agent Bridge  |      |
              |  | (Antigravity CLI) |      |
              |  | - Code refactoring|      |
              |  | - File operations |      |
              |  | - Build & test    |      |
              |  +───────────────────+      |
              |                             |
              |  +───────────────────+      |
              |  | Chromium (headless|      |
              |  | Playwright in RAM)|      |
              |  | - Web navigation  |      |
              |  | - Content extract |      |
              |  | - Screenshots     |      |
              |  +───────────────────+      |
              +─────────────────────────────+
                            |
                   [0x02] Audio frames
                   (24kHz PCM Aoede voice)
                            |
                            v
                     WireGuard Tunnel
                            |
                            v
                   ANDROID CLIENT SPEAKER
                   (Oboe C++ playback)
```

---

## Latency Budget

End-to-end latency from user speech to first audible response byte:

| Segment | Target | Measured |
| :--- | :--- | :--- |
| Microphone capture + framing | < 10 ms | 8 ms |
| WireGuard tunnel (LAN) | < 5 ms | 2-4 ms |
| WireGuard tunnel (WAN, same region) | < 50 ms | 30-45 ms |
| Tailscale relay (DERP, worst case) | < 200 ms | 80-180 ms |
| Gemini Live VAD + STT | < 300 ms | 200-350 ms |
| Gemini Live LLM inference | < 200 ms | 100-250 ms |
| Tool execution (Win32 in-memory) | < 5 ms | 0.3-2 ms |
| Gemini Live TTS first byte | < 200 ms | 150-300 ms |
| **Total (direct voice, no tools)** | **< 500 ms** | **370-500 ms** |
| **Total (with Computer Use)** | **< 3.5 s** | **2.8-3.5 s** |

---

## Core Components

### Host Daemon (Windows)

**`host-windows/nexo_daemon.py`** -- The unified Windows daemon providing:

- **Win32 Native Engine**: Direct `ctypes` calls to `user32.dll`, `gdi32.dll`, and `kernel32.dll` for mouse, keyboard, window management, and screen capture. Zero process spawning, zero script execution. All operations execute in < 2 ms.
- **Gemini Live Relay**: Bidirectional WebSocket connection to `wss://generativelanguage.googleapis.com` with the Multimodal Live API. Handles audio streaming, tool call dispatch, and voice synthesis relay.
- **MCP Server (stdio)**: JSON-RPC 2.0 interface for the Antigravity CLI (`agy`) to consume Win32 tools as a Model Context Protocol server.

**`host-windows/host_orchestrator.py`** -- Alternative gateway with Playwright-based Computer Use:

- Headless Chromium browser in RAM for web navigation, content extraction, and screenshots.
- PowerShell command execution with output capture.

**`host-windows/agy_agent_bridge.py`** -- AGY integration bridge:

- Dispatches autonomous engineering tasks to `agy agentapi`.
- Streams `stream-json` events in real-time for progress tracking.
- Enables background code refactoring, file operations, and build/test cycles without blocking the voice conversation.

### Mobile Client (Android)

**`client-mobile/test_client.py`** -- CLI streaming client for development and testing:

- Connects to the host daemon via WebSocket over Tailscale.
- Sends text prompts and receives 24kHz PCM audio responses.
- Saves responses as WAV files for inspection.

**`client-mobile/benchmark_latency.py`** -- End-to-end latency measurement suite:

- Measures connection time, time-to-first-audio (TTFA), and total operation duration.
- Tests the full Computer Use pipeline: voice command, web navigation, screenshot, and voice response.

### Infrastructure

**`scripts/wake_on_lan.py`** -- Remote boot via Wake-on-LAN / Intel vPro:

- Sends magic packets to start the Windows host from an Orange Pi on the LAN.
- Enables the mobile client to wake the workstation before establishing the voice session.

---

## Voice UX: Psychology-Informed Design

The Nexo voice interface is engineered around empirically validated principles from cognitive psychology, conversation analysis, and human-computer interaction research. These are not arbitrary design choices; each parameter maps to a specific finding in the literature.

### 1. Latency Perception

Three frameworks govern how users perceive response time:

**Doherty Threshold** (Doherty & Thadani, 1982): System response times must remain below **400 milliseconds** to maintain the user's cognitive flow. Above this threshold, the brain registers a conscious wait and productivity drops exponentially.

- Source: Doherty, W. J. & Thadani, A. J. (1982). [The Economic Value of Rapid Response Time](https://jlelliotton.blogspot.com/p/the-economic-value-of-rapid-response.html). *IBM Systems Journal*, 21(1).

**Nielsen Response Time Limits** (Nielsen, 1993): Three perceptual thresholds define the boundaries of human attention during interaction:

| Threshold | Perception | Design Action |
| :--- | :--- | :--- |
| 0.1 s (100 ms) | Instantaneous; direct manipulation | No feedback needed |
| 1.0 s | Flow preserved, delay noticed | Confirmation tone sufficient |
| 10 s | Attention disperses entirely | Continuous progress narration required |

- Source: Nielsen, J. (1993). [Response Times: The 3 Important Limits](https://www.nngroup.com/articles/response-times-3-important-limits/). *Usability Engineering*, Academic Press.

**Conversational Turn-Taking** (Sacks, Schegloff & Jefferson, 1974): In natural human conversation, the gap between turns averages **200-300 milliseconds**. Speakers anticipate turn endings using prosodic cues (falling intonation, deceleration) and begin planning their response before the current speaker finishes.

- Source: Sacks, H., Schegloff, E. A. & Jefferson, G. (1974). A Simplest Systematics for the Organization of Turn-Taking for Conversation. *Language*, 50(4), 696-735.
- Source: Stivers, T. et al. (2009). [Universals and Cultural Variation in Turn-Taking](https://www.pnas.org/doi/10.1073/pnas.0903616106). *PNAS*, 106(26), 10587-10592.

**Nexo Implementation**: The daemon emits an immediate acknowledgment cue ("Verificando o sistema.") within **< 300 ms** of detecting end-of-speech, before the LLM has completed inference.

### 2. Silence Tolerance

Research on telephone conversations shows that inter-turn silences exceeding **1 second** occur less than 5% of the time in natural speech. When a voice assistant exceeds this threshold, users interpret it as system failure.

| Silence Duration | User Perception |
| :--- | :--- |
| 0 - 0.5 s | Natural, conversational |
| 0.5 - 1.0 s | Delay noticed, tolerated |
| 1.0 - 2.0 s | Suspects failure, considers repeating |
| 2.0 - 4.0 s | Convinced system has crashed |
| > 5.0 s | Abandons interaction or speaks over |

- Source: Pearl, C. (2016). *Designing Voice User Interfaces: Principles of Conversational Experiences*. O'Reilly Media.
- Source: Levinson, S. C. & Torreira, F. (2015). [Timing in Turn-Taking and Its Implications for Processing Models of Language](https://www.frontiersin.org/articles/10.3389/fpsyg.2015.00731/full). *Frontiers in Psychology*, 6, 731.

**Nexo Implementation**: Maximum dead silence is capped at **1,500 ms**. If no content is available after **2,000 ms**, an emergency action-marker is emitted ("Processando os resultados...").

### 3. Cognitive Load Management

**Cognitive Load Theory** (Sweller, 1988) and the **Working Memory Model** (Baddeley & Hitch, 1974) establish that auditory information is processed by the phonological loop, which has severe capacity constraints:

- Working memory holds approximately **4 chunks** of information (Cowan, 2001).
- Auditory information decays within **1-2 seconds** unless actively rehearsed.
- Audio is serial and transient (unlike visual text, it cannot be re-read).

**Design Constraints**:
- Each progress cue contains a maximum of **12 words** (one semantic chunk).
- If 3+ cues accumulate in the queue, intermediate items are flushed; only the most recent and the final response are spoken.
- The **Redundancy Effect** (Sweller) prohibits reading aloud content already displayed on screen.

- Source: Sweller, J. (1988). Cognitive Load During Problem Solving: Effects on Learning. *Cognitive Science*, 12(2), 257-285.
- Source: Baddeley, A. D. & Hitch, G. J. (1974). Working Memory. *Psychology of Learning and Motivation*, 8, 47-89.
- Source: Cowan, N. (2001). [The Magical Number 4 in Short-Term Memory](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC2864034/). *Behavioral and Brain Sciences*, 24(1), 87-114.

### 4. Anti-Fatigue Controls

**Alert Fatigue** occurs when excessive auditory notifications cause habituation; the user begins ignoring all system output. Research at UC Irvine (Mark et al.) found that after a single interruption, it takes **over 20 minutes** to fully regain deep focus.

**Design Constraints**:
- Minimum gap between progress cues: **3,000 ms** (debounce).
- Maximum cues per task: **5** (absolute ceiling).
- If 3 cues fire within 5 seconds: automatic **5-second cooldown**.

**Cocktail Party Effect** (Cherry, 1953): Every notification competes with ambient sound for the user's selective auditory attention. The assistant uses a **consistent voice, timbre, and speaking rate** so the user can identify it instantly without cognitive processing overhead.

- Source: Cherry, E. C. (1953). [Some Experiments on the Recognition of Speech](https://doi.org/10.1121/1.1907229). *Journal of the Acoustical Society of America*, 25(5), 975-979.
- Source: Mark, G. et al. [The Cost of Interrupted Work](https://www.ics.uci.edu/~gmark/chi08-mark.pdf). *CHI 2008*, ACM.

### 5. Enterprise Persona

**Uncanny Valley** (Mori, 1970): Synthetic voices that are *almost* human but exhibit subtle imperfections trigger discomfort and distrust. In enterprise contexts, trust is built by **competence and transparency**, not by vocal humanness.

**Discourse Markers**: Research shows that in task-oriented contexts, filler words ("hmm", "uh", "let me think") decrease perceived competence and credibility. Nexo uses **action-objective markers** instead:

| Avoid (Reduces Trust) | Use Instead (Maintains Credibility) |
| :--- | :--- |
| "Hmm, deixa eu ver..." | "Consultando o sistema." |
| "Ah, um momento..." | "Validando as dependencias." |
| "Tipo, acho que..." | "Compilando o relatorio." |

- Source: Mori, M. (1970). The Uncanny Valley. *Energy*, 7(4), 33-35.
- Source: Clark, H. H. & Fox Tree, J. E. (2002). [Using *uh* and *um* in Spontaneous Speaking](https://doi.org/10.1016/S0010-0277(02)00017-3). *Cognition*, 84(1), 73-111.
- Source: Gnewuch, U. et al. (2022). The Effect of Conversational Agent Disfluency on User Perception and Trust. *ICIS 2022*.

**Progressive Disclosure** (Nielsen, 2006): Information is revealed incrementally, matching the user's current context. The first response is always a short summary; details are provided only on request.

- Source: Budiu, R. (2018). [Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/). Nielsen Norman Group.

### 6. Barge-in and Interruption

In natural conversation, interruptions are expected and serve essential communicative functions (repair, urgency, backchanneling). The system must detect user speech within **< 300 ms** and immediately:

1. Cut TTS playback (audio ducking by -12 to -18 dB within 20-50 ms).
2. Flush the entire audio queue.
3. Return the conversational floor to the user.

- Source: Jefferson, G. (1984). Notes on a Systematic Deployment of the Acknowledgement Tokens 'Yeah' and 'Mm hm'. *Papers in Linguistics*, 17, 197-216.
- Source: Gravano, A. & Hirschberg, J. (2011). [Turn-Taking Cues in Task-Oriented Dialogue](https://doi.org/10.1016/j.csl.2010.10.003). *Computer Speech & Language*, 25(3), 601-634.

---

## Dynamic Audio Queue

The Dynamic Audio Queue is the runtime mechanism that implements the psychology-informed design principles above. It operates as a FIFO priority queue between the tool-event stream and the TTS player:

```
[ User Speech ]
       |
[ STT / VAD ] ────> [ Immediate ACK Cue ] ──> [ Audio Priority Queue ]
       |                                              |
[ Nexo Daemon ]                                       |
  |-- Step 1: list_dir  ───> [ "Listando..." ]  ─────>|
  |-- Step 2: view_file ───> [ "Lendo X..." ]   ─────>|
  |-- Step 3: FINAL     ───> [ FLUSH + Summary ] ─────>|
       |                                              |
       v                                       [ TTS Player Worker ]
[ AGY agentapi ]                                      |
  (transcript_full.jsonl)                      (Audio without gaps)
```

**Key behaviors**:

- **Immediate ACK**: First cue enqueued at `t=0`, before LLM inference completes.
- **Debounce**: Minimum 3-second gap between cues prevents chatter fatigue.
- **Queue Flush on Completion**: When the final response arrives, all pending intermediate cues are discarded; only the summary is spoken.
- **Barge-in Flush**: User speech triggers immediate `queue.clear()` and TTS cancellation.

---

## Security Model

Nexo follows a defense-in-depth security architecture:

1. **No Hardcoded Credentials**: All API keys, tokens, and secrets are loaded exclusively from environment variables. The codebase contains zero embedded credentials.

2. **Transport Encryption**: All communication between the mobile client and the host daemon traverses a WireGuard tunnel (Tailscale tsnet). Traffic is encrypted end-to-end with Curve25519, ChaCha20-Poly1305, and BLAKE2s.

3. **Policy Engine**: The daemon enforces a strict allowlist of permitted operations, workspaces, and binaries via `policy.toml`. Commands outside the allowlist are rejected before execution.

4. **Non-Privileged Execution**: The daemon runs as a userspace service (non-root, non-admin). It has no access to system-wide resources beyond the user session.

5. **Deterministic Dispatch**: The daemon never interprets natural language directly. It is a mechanical dispatcher: Gemini issues structured tool calls; the daemon validates them against the policy and executes only pre-declared functions.

### Environment Variables

| Variable | Required | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Yes | Google AI Studio or Vertex AI API key for Gemini Multimodal Live |
| `TAILSCALE_AUTHKEY` | No | Tailscale auth key for automated node registration |

---

## Configuration

### Voice UX Profile (`voice_ux_profile.toml`)

```toml
[latency]
ack_deadline_ms = 300
doherty_ceiling_ms = 400
nielsen_flow_ms = 1000
target_turn_gap_ms = 250

[silence]
max_dead_ms = 1500
emergency_filler_ms = 2000
abandon_risk_ms = 4000

[queue]
max_cue_words = 12
max_queued_cues = 3
min_gap_between_cues_ms = 3000
max_cues_per_task = 5
flush_on_final_response = true

[persona]
name = "Nexo"
style = "guide"
transparency = true
voice_model = "pt-BR-Neural2-A"
speaking_rate = 1.1

[barge_in]
enabled = true
vad_sensitivity = 0.6
flush_queue = true
min_speech_ms = 300
```

### Policy Engine (`policy.toml`)

```toml
[workspaces]
allowed = ["C:\\Users\\Aluno\\nexo-daemon", "C:\\Users\\Aluno\\projects"]
denied = ["C:\\Windows", "C:\\Program Files"]

[binaries]
allowlist = ["agy.exe", "python.exe", "node.exe", "cargo.exe", "git.exe"]

[risk]
max_level = "L1"
```

---

## Deployment

### Prerequisites

- Windows 10/11 (x64)
- Python 3.11+
- Tailscale installed and authenticated
- Chromium (installed automatically by Playwright)

### 1. Host Setup (Windows)

```powershell
# Clone the repository
git clone https://github.com/multi-forge/nexo.git
cd nexo/host-windows

# Set environment variables
$env:GEMINI_API_KEY = "<your-api-key>"

# Run automated setup
.\setup_windows.ps1

# Start the daemon
python nexo_daemon.py
```

### 2. Mobile Client (Android / Termux)

```bash
# Set the host IP (Tailscale IP of the Windows machine)
export NEXO_HOST="ws://<tailscale-ip>:8765"

# Run the test client
python client-mobile/test_client.py
```

### 3. Remote Boot (Optional)

```bash
# From Orange Pi or any LAN device
python scripts/wake_on_lan.py AA:BB:CC:DD:EE:FF
```

---

## Repository Structure

```
nexo/
├── README.md                              # This document
├── docs/                                  # Technical specifications
│   ├── NEXO-SPEC-COMPLETA.md             # Master architecture specification
│   ├── nexo-event-architecture.md        # Event system and async task architecture
│   ├── nexo-latency-map.md              # Zero-copy latency map
│   ├── nexo-mcp-stack.md               # MCP server catalog and specification
│   ├── nexo-prompt-engineering.md       # Prompt engineering and voice calibration
│   ├── nexo-streaming-architecture.md   # NDJSON and WebSocket streaming protocol
│   └── VOICE-UX-PSYCHOLOGY.md           # Psychology-informed Voice UX specification
│
├── host-windows/                          # Windows host daemon and tools
│   ├── nexo_daemon.py                    # Unified daemon: Win32 engine + Gemini Live + MCP
│   ├── host_orchestrator.py              # WebSocket gateway with Playwright Computer Use
│   ├── agy_agent_bridge.py              # Antigravity CLI (AGY) integration bridge
│   ├── mcp_windows_computer_use.py      # MCP server for desktop automation
│   ├── setup_windows.ps1                # Automated Windows setup script
│   └── requirements.txt                 # Python dependencies
│
├── client-mobile/                         # Mobile clients and benchmarks
│   ├── test_client.py                    # CLI streaming test client
│   └── benchmark_latency.py             # End-to-end latency measurement suite
│
└── scripts/                               # Automation and infrastructure
    └── wake_on_lan.py                    # Remote boot via WoL / Intel vPro
```

---

## Academic References

The following research informs the design decisions documented in this repository:

| ID | Reference | Contribution |
| :--- | :--- | :--- |
| 1 | Doherty, W. J. & Thadani, A. J. (1982). The Economic Value of Rapid Response Time. *IBM Systems Journal*, 21(1). | 400 ms threshold for cognitive flow |
| 2 | Nielsen, J. (1993). *Usability Engineering*. Academic Press. | 0.1s / 1.0s / 10s response time limits |
| 3 | Sacks, H., Schegloff, E. A. & Jefferson, G. (1974). A Simplest Systematics for Turn-Taking. *Language*, 50(4). | 200-300 ms conversational turn gap |
| 4 | Stivers, T. et al. (2009). Universals and Cultural Variation in Turn-Taking. *PNAS*, 106(26). | Cross-linguistic turn-taking norms |
| 5 | Sweller, J. (1988). Cognitive Load During Problem Solving. *Cognitive Science*, 12(2). | Cognitive Load Theory |
| 6 | Baddeley, A. D. & Hitch, G. J. (1974). Working Memory. *Psychology of Learning and Motivation*, 8. | Phonological loop constraints |
| 7 | Cowan, N. (2001). The Magical Number 4 in Short-Term Memory. *Behavioral and Brain Sciences*, 24(1). | Working memory capacity limits |
| 8 | Cherry, E. C. (1953). Some Experiments on the Recognition of Speech. *JASA*, 25(5). | Cocktail Party Effect |
| 9 | Mori, M. (1970). The Uncanny Valley. *Energy*, 7(4). | Synthetic voice trust dynamics |
| 10 | Clark, H. H. & Fox Tree, J. E. (2002). Using *uh* and *um* in Spontaneous Speaking. *Cognition*, 84(1). | Filler word perception |
| 11 | Mark, G. et al. (2008). The Cost of Interrupted Work. *CHI 2008*, ACM. | 20+ minutes to recover deep focus |
| 12 | Pearl, C. (2016). *Designing Voice User Interfaces*. O'Reilly Media. | Voice UI design patterns |
| 13 | Levinson, S. C. & Torreira, F. (2015). Timing in Turn-Taking. *Frontiers in Psychology*, 6. | Turn-taking timing models |
| 14 | Gravano, A. & Hirschberg, J. (2011). Turn-Taking Cues. *Computer Speech & Language*, 25(3). | Prosodic cue analysis |
| 15 | Gnewuch, U. et al. (2022). Conversational Agent Disfluency. *ICIS 2022*. | Filler words reduce enterprise trust |
| 16 | Rodden, K. et al. (2010). Measuring the User Experience at Scale: HEART Framework. *CHI 2010*, ACM. | UX measurement framework |
| 17 | Budiu, R. (2018). Progressive Disclosure. Nielsen Norman Group. | Incremental information revelation |

---

## License

Proprietary. All rights reserved.

This software is confidential and intended for internal development use only.
Unauthorized copying, distribution, or modification is strictly prohibited.
