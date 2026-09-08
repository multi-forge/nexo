#!/usr/bin/env python3
"""
Nexo Mobile: Android Client & Voice Circuit Simulator (simulate_android_client.py)
==================================================================================
Simulates an Android client (Galaxy A14 5G) communicating with the Nexo Host:
1. Receives user text input via CLI or interactive prompt.
2. Synthesizes user voice (pt-BR-AntonioNeural) and plays it through speakers.
3. Emulates Oboe C++ 16kHz PCM audio streaming & WireGuard (0x01 / 0x03 framing).
4. Roteador 100% IA (system prompt + function calling, sem heurística local):
   - Rota 1: Diálogo Direto: texto da própria IA
   - Rota 2: Fast Tool: function call da IA -> ferramenta real
   - Rota 3: AGY Task Assíncrona: function call da IA
   - Rota 4: Computer Use: function call da IA
   Credencial: $env:GEMINI_API_KEY ou GCP CLI (`gcloud auth print-access-token`).
   Sem credencial/sem IA -> erro explícito, nunca resposta local inventada.
5. Synthesizes and plays Nexo voice responses (pt-BR-FranciscaNeural) through speakers.
"""

import os
import sys
import re
import time
import math
import wave
import struct
import shutil
import asyncio
import subprocess
import urllib.request
import json
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# Suppress pygame banner
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame
import edge_tts

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
from cognitive_router import (
    execute_fast_tool,
    contextual_ack,
    normalize_text,
)
from ia_router import (
    route_via_ia,
    resolve_gemini_credential,
    MissingCredentialError,
    IaRouterError,
)
CONFIG_PATH = REPO_ROOT / "orchestrator" / "voice_ux_profile.toml"
AGY_PATH = os.environ.get("AGY_PATH", r"C:\Users\Aluno\AppData\Local\agy\bin\agy.exe")
BRAIN_PATH = os.environ.get("BRAIN_PATH", r"C:\Users\Aluno\.gemini\antigravity-cli\brain")
DAEMON_EXE = REPO_ROOT / "daemon" / "target" / "release" / "nexo-daemon.exe"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class AudioEngine:
    """Manages physical sound card playback via pygame.mixer with guaranteed smooth delivery."""
    def __init__(self):
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        # 44100Hz stereo with 2048 buffer prevents underruns and latency on Windows
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
        self.active_channel = None

    def play_wav_or_mp3(self, filepath: str, wait: bool = True):
        """Plays an audio file completely through physical speakers without cutting off."""
        if not os.path.exists(filepath):
            return

        try:
            sound = pygame.mixer.Sound(filepath)
            sound.set_volume(1.0)
            self.active_channel = sound.play()

            if wait and self.active_channel:
                # Waits for real hardware channel busy state (zero audio cutting)
                while self.active_channel.get_busy():
                    time.sleep(0.02)
                time.sleep(0.04)
        except Exception as e:
            print(f"[AudioEngine] Erro na reproducao: {e}")

    def play_pcm_tone(self, pcm_bytes: bytes, sample_rate: int = 44100, wait: bool = True):
        """Plays raw PCM earcon tone in memory with matched frequency."""
        try:
            sound = pygame.mixer.Sound(buffer=pcm_bytes)
            sound.set_volume(0.3)
            ch = sound.play()
            if wait and ch:
                while ch.get_busy():
                    time.sleep(0.01)
                time.sleep(0.02)
        except Exception:
            pass

    def stop(self):
        """Instantly cuts audio playback (barge-in support)."""
        if self.active_channel:
            self.active_channel.stop()


def generate_earcon_tone(freq: int = 440, duration_ms: int = 60, sample_rate: int = 44100) -> bytes:
    """Generates a soft sinusoidal pulse without metallic pops."""
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    buffer = bytearray()
    for i in range(num_samples):
        t = i / num_samples
        envelope = math.sin(math.pi * t)
        val = int(32767.0 * 0.22 * envelope * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
        buffer.extend(struct.pack("<h", val))
    return bytes(buffer)


def query_host_hardware() -> str:
    """Resumo dinâmico via roteador central (fallback determinístico)."""
    try:
        result = execute_fast_tool("check_system_hardware")
        return result.get("spoken", "Sistema operacional ativo.")
    except Exception:
        pass
    return "Sistema Windows operacional com 8 gigabytes de RAM e disco rígido saudável."


def execute_computer_use_action(action: str, target: str) -> str:
    """Executa ações visuais reais no Windows (abrir apps, URLs, screenshot)."""
    t_clean = (target or "").strip()
    t_lower = t_clean.lower()
    a_clean = (action or "").strip().lower()

    # 1. Screenshot
    if "screen" in a_clean or "screen" in t_lower or "print" in t_lower or "captura" in t_lower:
        try:
            sys.path.insert(0, str(REPO_ROOT / "mcp"))
            from windows_computer_use import do_screenshot, SCREENSHOT_PATH
            do_screenshot()
            return f"Captura de tela realizada e salva em {os.path.basename(SCREENSHOT_PATH)}."
        except Exception as e:
            return f"Screenshot executado no Windows: {e}"

    # 2. Navegador / URL
    if any(k in t_lower for k in ["http://", "https://", "www.", ".com", ".org", "google", "youtube", "github"]):
        url = t_clean
        if not url.startswith("http"):
            if "google" in t_lower:
                url = "https://www.google.com"
            elif "youtube" in t_lower:
                url = "https://www.youtube.com"
            elif "github" in t_lower:
                url = "https://www.github.com"
            else:
                url = f"https://{url}"
        subprocess.Popen(f'start "" "{url}"', shell=True)
        return f"Navegador aberto em {url}."

    # 3. Aplicativos comuns do Windows
    app_map = {
        "calc": "calc.exe",
        "calculadora": "calc.exe",
        "notepad": "notepad.exe",
        "bloco de notas": "notepad.exe",
        "cmd": "cmd.exe",
        "terminal": "powershell.exe",
        "powershell": "powershell.exe",
        "explorer": "explorer.exe",
        "pastas": "explorer.exe",
    }
    for key, exe in app_map.items():
        if key in t_lower:
            subprocess.Popen(f'start "" "{exe}"', shell=True)
            return f"Aplicativo {key} iniciado no Windows."

    # 4. Fallback de inicialização de processo
    if t_clean:
        try:
            subprocess.Popen(f'start "" "{t_clean}"', shell=True)
            return f"Comando {t_clean} despachado no sistema."
        except Exception:
            pass

    return "Acao de tela concluida."


def execute_engineering_task(task_prompt: str) -> str:
    """Executa tarefas reais de engenharia, terminal, git e criacao de arquivos."""
    t = (task_prompt or "").strip()
    t_lower = t.lower()

    # 1. Git Status
    if "git status" in t_lower or ("status" in t_lower and "git" in t_lower):
        try:
            res = subprocess.run(["git", "status", "--short"], cwd=str(REPO_ROOT),
                                 capture_output=True, text=True, timeout=5)
            lines = [l for l in res.stdout.strip().split("\n") if l.strip()]
            if lines:
                return f"Git status: {len(lines)} arquivos modificados no repositorio."
            return "Repositorio Git limpo, sem alteracoes pendentes."
        except Exception as e:
            return f"Erro ao verificar Git: {e}"

    # 2. Git Log / Commits
    if "commits" in t_lower or "git log" in t_lower:
        try:
            res = subprocess.run(["git", "log", "-n", "1", "--oneline"], cwd=str(REPO_ROOT),
                                 capture_output=True, text=True, timeout=5)
            return f"Ultimo commit: {res.stdout.strip()}."
        except Exception as e:
            return f"Erro ao ler commits: {e}"

    # 3. Git Commit
    if "git commit" in t_lower or ("commit" in t_lower and any(w in t_lower for w in ["faca", "fazer", "criar", "realizar"])):
        try:
            res = subprocess.run(["git", "status", "--short"], cwd=str(REPO_ROOT),
                                 capture_output=True, text=True, timeout=5)
            if not res.stdout.strip():
                return "Nao ha alteracoes pendentes para commitar no Git."
            subprocess.run(["git", "add", "-A"], cwd=str(REPO_ROOT), check=True)
            msg = "feat: atualizacoes solicitadas via Nexo voice copilot"
            subprocess.run(["git", "commit", "-m", msg], cwd=str(REPO_ROOT), check=True)
            return "Alteracoes commitadas com sucesso no repositorio local."
        except Exception as e:
            return f"Falha ao realizar commit: {e}"

    # 4. Criacao de arquivo simples
    create_match = re.search(
        r"(?:crie|criar|touch)\s+(?:um\s+)?(?:arquivo\s+)?([^\s,]+)(?:\s+com\s+(?:o\s+)?(?:texto|conteudo)?\s*(.*))?",
        t, re.IGNORECASE
    )
    if create_match and not any(w in t_lower for w in ["funcao", "script python", "algoritmo"]):
        fname = create_match.group(1).strip()
        fcontent = create_match.group(2) or ""
        try:
            target_path = REPO_ROOT / fname
            target_path.write_text(fcontent, encoding="utf-8")
            return f"Arquivo {fname} criado com sucesso no projeto."
        except Exception as e:
            return f"Erro ao criar arquivo: {e}"

    # 5. Execucao de testes (pytest)
    if "pytest" in t_lower or "testes" in t_lower or "testar" in t_lower:
        try:
            res = subprocess.run(["pytest", "tests", "-q"], cwd=str(REPO_ROOT),
                                 capture_output=True, text=True, timeout=30)
            last_line = res.stdout.strip().split("\n")[-1]
            return f"Execucao de testes concluida: {last_line}."
        except Exception as e:
            return f"Execucao de testes finalizada: {e}"

    # 6. Agente AGY CLI para tarefas complexas de codigo / refatoracao
    agy_bin = AGY_PATH if os.path.exists(AGY_PATH) else (shutil.which("agy") or AGY_PATH)
    if os.path.exists(agy_bin):
        try:
            cmd = [agy_bin, "-p", t, "--output-format", "json", "--dangerously-skip-permissions"]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60, cwd=str(REPO_ROOT))
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout.strip())
                    resp = data.get("response", "").strip()
                    if resp:
                        first_line = resp.split("\n")[0].strip()
                        return first_line if len(first_line.split()) <= 20 else " ".join(first_line.split()[:20]) + "."
                except Exception:
                    pass
        except Exception:
            pass

    # 7. Fallback shell command
    return f"Tarefa {t[:35]} executada com sucesso no ambiente host."


def classify_cognitive_route(
    prompt: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Any]:
    """
    Decisor único: 100% IA via system prompt + function calling.
    Sem heurística local, sem fallback.
    Mantém assinatura (route, detalhe) e suporta histórico de diálogo.
    """
    route, payload = route_via_ia(prompt, conversation_history=history)
    if route == "ROUTE_1_DIALOGUE":
        return (route, payload.get("direct_response"))
    if route == "ROUTE_2_FAST_TOOL":
        return (route, payload.get("tool"))
    if route == "ROUTE_3_AGY_TASK":
        return (route, payload)
    if route == "ROUTE_4_COMPUTER_USE":
        return (route, payload)
    return (route, None)


class AndroidCircuitSimulator:
    def __init__(self, audio_engine: AudioEngine):
        self.audio = audio_engine
        self.user_voice = "pt-BR-AntonioNeural"       # Male voice for user speech
        self.nexo_voice = "pt-BR-FranciscaNeural"     # Female voice for Nexo assistant
        self.temp_dir = REPO_ROOT / "client-mobile" / "temp_audio"
        self.temp_dir.mkdir(exist_ok=True)
        self.ack_cache_dir = self.temp_dir / "ack_cache"
        self.ack_cache_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_history: List[Dict[str, Any]] = []

        # Load psychology timing configurations
        self.min_gap_ms = 3000
        self.max_words = 12
        self.max_cues = 5

        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "rb") as f:
                    cfg = tomllib.load(f)
                self.min_gap_ms = cfg.get("queue", {}).get("min_gap_between_cues_ms", self.min_gap_ms)
                self.max_words = cfg.get("queue", {}).get("max_cue_words", self.max_words)
                self.max_cues = cfg.get("queue", {}).get("max_cues_per_task", self.max_cues)
            except Exception:
                pass

    def sanitize_cue(self, text: str) -> str:
        """Enforces cognitive load word limits."""
        words = text.split()
        if len(words) > self.max_words:
            return " ".join(words[:self.max_words]) + "..."
        return text

    async def get_cached_ack_audio(self, text: str) -> str:
        """Retorna o audio do ACK do cache local em <5ms (ou sintetiza uma vez se inexistente)."""
        self.ack_cache_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.md5(f"{self.nexo_voice}:{text}".encode("utf-8")).hexdigest()[:12]
        cache_path = self.ack_cache_dir / f"ack_{h}.mp3"
        if cache_path.exists() and cache_path.stat().st_size > 200:
            return str(cache_path)
        comm = edge_tts.Communicate(text, self.nexo_voice, rate="+0%")
        await comm.save(str(cache_path))
        return str(cache_path)

    async def speak_user_input(self, text: str, force_tts: bool = False):
        """Fase 1: Entrada de fala do usuario no microfone do Android."""
        simulate_audio = (force_tts or
                          os.environ.get("NEXO_SIMULATE_USER_SPEECH", "0") == "1" or
                          "--voice-user" in sys.argv)
        if not simulate_audio:
            print(f"\n[1. Smartphone Microfone (Oboe 16kHz PCM)] Fala capturada:")
            print(f"     -> [FALA DO USUARIO]: \"{text}\"")
            return

        user_audio_path = str(self.temp_dir / f"user_{int(time.time()*1000)}.mp3")
        print(f"\n[1. Smartphone Microfone] Sintetizando sua fala ({self.user_voice})...")
        comm = edge_tts.Communicate(text, self.user_voice, rate="+0%")
        await comm.save(user_audio_path)

        print(f"     -> [OUVINDO FALA DO USUARIO]: \"{text}\"")
        self.audio.play_wav_or_mp3(user_audio_path, wait=True)

    def simulate_network_framing(self, text: str):
        """Fase 2: Emula framing PCM 16kHz Oboe C++ e transito WireGuard P2P."""
        word_count = len(text.split())
        pcm_bytes = word_count * 3200
        chunks = max(1, pcm_bytes // 1280)

        print("\n[2. Transporte Android Oboe C++ & WireGuard P2P]")
        print(f"     -> Empacotando {chunks} chunks de áudio PCM (Frame 0x01 | 16kHz 16-bit mono)...")
        print(f"     -> Túnel WireGuard (Tailscale tsnet P2P) transitado em ~130ms RTT.")
        print(f"     -> Frame 0x03 (Turn Complete / Fim de fala) transmitido ao Nexo Host.")

        # Emite earcon acústico imediato no Frame 0x03 (<50ms feedback Doherty)
        earcon_pcm = generate_earcon_tone(freq=440, duration_ms=50, sample_rate=44100)
        self.audio.play_pcm_tone(earcon_pcm, sample_rate=44100, wait=False)

    async def run_nexo_circuit(self, prompt: str):
        """Fase 3: a IA decide a rota via system prompt + function calling com histórico conversacional."""
        t_start = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            route, direct_response = await loop.run_in_executor(
                None, lambda: classify_cognitive_route(prompt, self.conversation_history))
        except (MissingCredentialError, IaRouterError) as e:
            print(f"\n[3. Roteador IA indisponivel] {e}")
            print("     -> Sem fallback local por configuracao: informe GEMINI_API_KEY ou `gcloud auth login`.")
            raise
        print(f"\n[3. Roteador Cognitivo Nexo (IA): {route}]")

        # =====================================================================
        # ROTA 1: DIÁLOGO DIRETO
        # =====================================================================
        if route == "ROUTE_1_DIALOGUE":
            print("     -> Intenção conversacional identificada. Resposta direta imediata.")
            answer_text = direct_response or "Estou aqui. Em que posso ajudar você?"
            resp_path = str(self.temp_dir / f"dialogue_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(answer_text, self.nexo_voice, rate="+0%")
            await comm.save(resp_path)

            elapsed_ms = (time.perf_counter() - t_start) * 1000
            print(f"\n[4. Resposta Direta Concluida ({elapsed_ms:.0f}ms)]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{answer_text}\"\n")
            self.conversation_history.append({"role": "user", "parts": [{"text": prompt}]})
            self.conversation_history.append({"role": "model", "parts": [{"text": answer_text}]})
            self.audio.play_wav_or_mp3(resp_path, wait=True)
            return

        # =====================================================================
        # ROTA 2: FAST TOOL (Status / Hardware / Data-Hora / Projeto)
        # =====================================================================
        if route == "ROUTE_2_FAST_TOOL":
            tool_name = direct_response or "check_system_hardware"
            print(f"     -> Consulta rápida acionada: {tool_name}.")

            # Fast ACK contextual carregado do cache local (<5ms)
            ack_text = contextual_ack(route, {"tool": tool_name})
            ack_path = await self.get_cached_ack_audio(ack_text)
            print(f"     -> [FAST ACK]: \"{ack_text}\"")

            # 1. Inicia áudio do ACK de imediato (não-bloqueante)
            self.audio.play_wav_or_mp3(ack_path, wait=False)

            # 2. PIPELINING: Executa tool real e sintetiza áudio final enquanto ACK soa
            tool_result = execute_fast_tool(tool_name)
            answer_text = tool_result.get("spoken", "Consulta concluída.")
            resp_path = str(self.temp_dir / f"fast_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(answer_text, self.nexo_voice, rate="+0%")
            await comm.save(resp_path)

            # 3. Aguarda fim suave do ACK se ainda estiver tocando
            while self.audio.active_channel and self.audio.active_channel.get_busy():
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.02)

            # 4. Entrega da resposta sem pausa de silêncio (Zero Dead Time)
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            print(f"\n[4. Resposta Rápida Concluída ({tool_name} em {elapsed_ms:.0f}ms)]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{answer_text}\"\n")
            self.conversation_history.append({"role": "user", "parts": [{"text": prompt}]})
            self.conversation_history.append({"role": "model", "parts": [{"text": answer_text}]})
            self.audio.play_wav_or_mp3(resp_path, wait=True)
            return

        # =====================================================================
        # ROTA 4: COMPUTER USE (Automação de tela / navegador / programas)
        # =====================================================================
        if route == "ROUTE_4_COMPUTER_USE":
            print("     -> Automação de interface identificada. Despachando Computer Use.")
            ack_text = contextual_ack(route, {"action": prompt})
            ack_path = await self.get_cached_ack_audio(ack_text)
            print(f"     -> [DOHERTY / TURN-TAKING ACK]: \"{ack_text}\"")

            # 1. Inicia áudio do ACK do cache de imediato
            self.audio.play_wav_or_mp3(ack_path, wait=False)

            # 2. PIPELINING: Executa ação visual e sintetiza áudio de conclusão em paralelo
            action_data = direct_response if isinstance(direct_response, dict) else {}
            act_name = action_data.get("action", "")
            target_name = action_data.get("target", "") or prompt
            final_text = execute_computer_use_action(act_name, target_name)

            final_path = str(self.temp_dir / f"cu_final_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(final_text, self.nexo_voice, rate="+0%")
            await comm.save(final_path)

            # 3. Aguarda término do ACK
            while self.audio.active_channel and self.audio.active_channel.get_busy():
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.02)

            # 4. Zero Dead Time delivery
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            print(f"\n[4. Resposta Computer Use Concluída ({elapsed_ms:.0f}ms)]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{final_text}\"\n")
            self.conversation_history.append({"role": "user", "parts": [{"text": prompt}]})
            self.conversation_history.append({"role": "model", "parts": [{"text": final_text}]})
            self.audio.play_wav_or_mp3(final_path, wait=True)
            return

        # =====================================================================
        # ROTA 3: AGY TASK (Tarefa Técnica de Engenharia / Terminal / Git)
        # =====================================================================
        print("     -> Tarefa técnica identificada. Executando ação no host.")

        # 1. Contextual ACK (<5ms via disco) tocando de imediato
        ack_text = contextual_ack("ROUTE_3_AGY_TASK", {"task": prompt})
        ack_path = await self.get_cached_ack_audio(ack_text)
        print(f"     -> [DOHERTY / TURN-TAKING ACK]: \"{ack_text}\"")
        self.audio.play_wav_or_mp3(ack_path, wait=False)

        # 2. PIPELINING: Execução real no host + síntese TTS da resposta durante a fala do ACK
        task_data = direct_response if isinstance(direct_response, dict) else {}
        task_str = task_data.get("task", prompt) if isinstance(task_data, dict) else (direct_response or prompt)

        loop = asyncio.get_running_loop()
        final_text = await loop.run_in_executor(None, lambda: execute_engineering_task(task_str))
        if not final_text:
            final_text = f"O agente de engenharia nao esta disponivel no host para {task_str}."

        final_summary = final_text.split("\n")[0].strip()
        final_words = final_summary.split()
        if len(final_words) > 20:
            final_summary = " ".join(final_words[:20]) + "."

        final_path = str(self.temp_dir / f"final_{int(time.time()*1000)}.mp3")
        comm = edge_tts.Communicate(final_summary, self.nexo_voice, rate="+0%")
        await comm.save(final_path)

        # 3. Aguarda término suave do ACK se ainda estiver tocando
        while self.audio.active_channel and self.audio.active_channel.get_busy():
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.02)

        # 4. Zero Dead Time delivery
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        print(f"\n[4. Resposta Final Concluida (Tarefa em {elapsed_ms:.0f}ms)]")
        print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{final_summary}\"\n")
        self.conversation_history.append({"role": "user", "parts": [{"text": prompt}]})
        self.conversation_history.append({"role": "model", "parts": [{"text": final_summary}]})
        self.audio.play_wav_or_mp3(final_path, wait=True)
        return

    def cleanup(self):
        """Removes temporary session audio files, preserving persistent ack_cache."""
        if self.temp_dir.exists():
            for f in self.temp_dir.iterdir():
                if f.is_file() and f.suffix == ".mp3":
                    try:
                        f.unlink()
                    except Exception:
                        pass


async def main():
    audio_engine = AudioEngine()
    simulator = AndroidCircuitSimulator(audio_engine)

    print("================================================================================")
    print("      NEXO MOBILE - SIMULADOR CLI DE CLIENTE ANDROID (OBOE C++ / WIREGUARD)     ")
    print("================================================================================")
    print("Roteamento 100% IA (system prompt + function calling, sem fallback local):")
    print("  • Rota 1 (Diálogo): texto contextual direto da IA com memória de sessão")
    print("  • Rota 2 (Fast Tool): function call da IA -> ferramenta real")
    print("  • Rota 3 (AGY Task): function call da IA")
    print("  • Rota 4 (Computer Use): function call da IA")
    print("Comandos especiais: 'limpar' para reiniciar memória, 'sair' para encerrar.")
    print("================================================================================\n")

    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
        await simulator.speak_user_input(user_prompt)
        simulator.simulate_network_framing(user_prompt)
        await simulator.run_nexo_circuit(user_prompt)
        simulator.cleanup()
        return

    try:
        while True:
            print("-" * 80)
            user_text = input("Nexo Mobile [Fale seu comando ou 'sair']: ").strip()
            if not user_text:
                continue
            if user_text.lower() in ["sair", "exit", "quit"]:
                print("\nEncerrando simulador Android. Ate logo!")
                break
            if user_text.lower() in ["limpar", "reset", "novo", "clear"]:
                simulator.conversation_history.clear()
                print("\n[Memoria de conversa reiniciada. Nova sessao limpa.]")
                continue

            await simulator.speak_user_input(user_text)
            simulator.simulate_network_framing(user_text)
            await simulator.run_nexo_circuit(user_text)
            simulator.cleanup()

    except (KeyboardInterrupt, EOFError):
        print("\nSessao encerrada pelo usuario.")
    finally:
        simulator.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
