# 🎙️ Jarvis-Dev: Voice-First Autonomous AI Engineering OS

> **Engenharia de Software Autônoma e Real Computer Use via Voz em Tempo Real no seu Fone de Ouvido.**

O **Jarvis-Dev** é uma arquitetura de orquestração de alto desempenho que transforma seu smartphone e seu PC Windows em uma estação de trabalho controlada 100% por voz e streaming multimodal.

---

## ⚡ Principais Capacidades

- 🎧 **Streaming Bidirecional de Áudio Nativo (Zero-Copy):** Oboe C++ (Android) ➔ WireGuard (Tailscale) ➔ Gemini 3.1 Live Preview (Aoede 24kHz).
- 🏎️ **Latência Ultra-Baixa:** ~130ms de rede P2P, ~370ms TTFA na voz direta e ~3.2s para Computer Use completo com navegação e print.
- 🛠️ **Real Computer Use no Windows (MCP Nativo):** Controle de mouse, teclado, janelas, screenshots e automação Chromium via Playwright em RAM.
- 🚀 **Antigravity CLI (AGY) em Background:** Disparo de tarefas pesadas de refatoração e desenvolvimento autônomo com notificação por voz sem travar a conversa.
- 🔌 **Intel vPro / Wake-on-LAN:** Inicialização remota do PC Host através de uma Orange Pi na LAN.

---

## 📁 Estrutura do Repositório

```
jarvis-dev/
├── docs/                                  # Especificações completas de arquitetura
│   ├── JARVIS-DEV-SPEC-COMPLETA.md        # Especificação técnica master
│   ├── jarvis-dev-latency-map.md          # Mapa de latência zero-copy
│   ├── jarvis-dev-event-architecture.md   # Arquitetura de eventos e tarefas assíncronas
│   ├── jarvis-dev-prompt-engineering.md   # Engenharia de prompts e calibração de voz
│   ├── jarvis-dev-streaming-architecture.md # Protocolo de streaming NDJSON e WebSocket
│   └── jarvis-dev-mcp-stack.md            # Catálogo e especificação de MCPs
│
├── host-windows/                          # Orquestrador do Host Windows
│   ├── host_orchestrator.py               # Gateway WebSocket + Gemini 3.1 Live + AGY Router
│   ├── mcp_windows_computer_use.py        # Servidor MCP para Real Computer Use (Win32)
│   ├── setup_windows.ps1                  # Script de instalação automatizada (PowerShell)
│   └── requirements.txt                   # Dependências Python
│
├── client-mobile/                         # Clientes Mobile e Testes
│   ├── test_client.py                     # Cliente Python CLI de streaming
│   ├── benchmark_latency.py               # Suíte de medição de latência ponta a ponta
│   └── android-spec/                      # Especificações do motor Oboe C++ para Android
│
└── scripts/                               # Scripts de automação e infraestrutura
    ├── wake_on_lan.py                     # Script para boot remoto via Orange Pi
    └── start_host.bat                     # Inicialização rápida no Windows
```

---

## 🚀 Como Executar

### 1. No Host Windows:
```powershell
cd host-windows
.\setup_windows.ps1
python host_orchestrator.py
```

### 2. No Celular (Termux ou App Android):
```bash
python client-mobile/test_client.py
```

---

## 🔒 Licença
Propriedade privada de desenvolvimento autônomo.
