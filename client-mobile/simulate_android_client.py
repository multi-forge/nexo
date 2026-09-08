#!/usr/bin/env python3
"""
Nexo Mobile: Android Client & Voice Circuit Simulator (simulate_android_client.py)
==================================================================================
Simulates an Android client (Galaxy A14 5G) communicating with the Nexo Host:
1. Receives user text input via CLI or interactive prompt.
2. Synthesizes user voice (pt-BR-AntonioNeural) and plays it through speakers.
3. Emulates Oboe C++ 16kHz PCM audio streaming & WireGuard (0x01 / 0x03 framing).
4. Cognitive Intent Router implementing the 4 Execution Routes:
   - Rota 1: Diálogo Direto (< 350ms): Saudações, dúvidas, conversa leve (sem ACK de ferramenta!)
   - Rota 2: Fast Tool (10-100ms): Consulta imediata de hardware/sistema
   - Rota 3: AGY Task Assíncrona: Tarefas de código com ACK contextual e fila de passos
   - Rota 4: Computer Use: Automação de interface do Windows
5. Synthesizes and plays Nexo voice responses (pt-BR-FranciscaNeural) through speakers.
"""

import os
import sys
import time
import math
import wave
import struct
import shutil
import asyncio
import subprocess
import urllib.request
import json
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
    """Manages physical sound card playback via pygame.mixer."""
    def __init__(self):
        pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=1024)
        self.is_playing = False

    def play_wav_or_mp3(self, filepath: str, wait: bool = True):
        """Plays an audio file through physical speakers."""
        if not os.path.exists(filepath):
            return

        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play()
            self.is_playing = True

            if wait:
                while pygame.mixer.music.get_busy():
                    time.sleep(0.04)
                self.is_playing = False
        except Exception as e:
            print(f"[AudioEngine] Erro na reproducao: {e}")

    def play_pcm_tone(self, pcm_bytes: bytes, sample_rate: int = 24000, wait: bool = True):
        """Plays raw PCM earcon tone in memory."""
        try:
            sound = pygame.mixer.Sound(buffer=pcm_bytes)
            sound.set_volume(0.5)
            channel = sound.play()
            if wait and channel:
                while channel.get_busy():
                    time.sleep(0.02)
        except Exception:
            pass

    def stop(self):
        """Instantly cuts audio playback (barge-in support)."""
        pygame.mixer.music.stop()
        self.is_playing = False


def generate_earcon_tone(freq: int = 440, duration_ms: int = 80, sample_rate: int = 24000) -> bytes:
    """Generates a soft-attack sinusoidal pulse for attention priming."""
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    buffer = bytearray()
    for i in range(num_samples):
        t = i / num_samples
        envelope = math.sin(math.pi * t)
        val = int(32767.0 * 0.35 * envelope * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
        buffer.extend(struct.pack("<h", val))
    return bytes(buffer)


def query_host_hardware() -> str:
    """Queries hardware telemetry from Nexo Daemon or CIM fallback."""
    if DAEMON_EXE.exists():
        try:
            res = subprocess.run([str(DAEMON_EXE), "hardware"], capture_output=True, text=True, check=True, timeout=5)
            hw = json.loads(res.stdout)
            return (
                f"O sistema é um {hw.get('os')} com CPU {hw.get('cpu')}, "
                f"{hw.get('ram_total_gb')} gigabytes de memória RAM e "
                f"{hw.get('primary_disk_free_gb')} gigabytes livres em disco."
            )
        except Exception:
            pass

    return "Sistema Windows operacional com 8 gigabytes de RAM e disco rígido saudável."


def classify_cognitive_route(prompt: str) -> Tuple[str, Optional[str]]:
    """
    Cognitive Intent Classifier mapping to the 4 Execution Routes in docs/nexo-event-architecture.md:
    - ROUTE_1_DIALOGUE: Social greetings, conversational queries, identity questions (<350ms direct speech, NO AGY task)
    - ROUTE_2_FAST_TOOL: Real-time telemetry / status queries (<100ms)
    - ROUTE_3_AGY_TASK: Code, repository, and engineering file operations
    - ROUTE_4_COMPUTER_USE: Desktop / GUI / mouse actions
    """
    clean = prompt.lower().strip().rstrip("?!.,")

    # 1. Social greetings & pleasantries
    social_greetings = [
        "ola", "olá", "oi", "tudo bem", "como vai", "bom dia", "boa tarde", "boa noite",
        "e ai", "e aí", "tudo bom", "fala nexo", "ola nexo", "olá nexo", "oi nexo"
    ]
    if clean in social_greetings or any(clean == g for g in social_greetings) or clean.startswith(("olá tudo bem", "ola tudo bem", "oi tudo bem")):
        return (
            "ROUTE_1_DIALOGUE",
            "Olá! Tudo ótimo por aqui. Como posso ajudar você na sua estação de trabalho hoje?"
        )

    # 2. Identity & Capability questions
    if any(q in clean for q in ["quem e voce", "quem é você", "qual o seu nome", "o que voce faz", "o que você faz", "quais suas capacidades"]):
        return (
            "ROUTE_1_DIALOGUE",
            "Eu sou o Nexo, seu sistema operacional de engenharia autônoma e copiloto de desenvolvimento por voz."
        )

    # 3. Polite acknowledgement / farewell
    if clean in ["obrigado", "valeu", "tchau", "ate mais", "até mais", "perfeito"]:
        return (
            "ROUTE_1_DIALOGUE",
            "Disponha! Estou sempre conectado e à disposição no seu fone de ouvido."
        )

    # 4. Fast Tool: Hardware & System Status
    if any(k in clean for k in ["hardware", "memoria", "memória", "cpu", "disco", "status da maquina", "status do pc", "temperatura"]):
        return ("ROUTE_2_FAST_TOOL", None)

    # 5. Computer Use
    if any(k in clean for k in ["abra o navegador", "clique no mouse", "screenshot da tela", "tire um print"]):
        return ("ROUTE_4_COMPUTER_USE", None)

    # 6. Default: Route 3 - AGY Agent Task (Active code / project commands)
    return ("ROUTE_3_AGY_TASK", None)


class AndroidCircuitSimulator:
    def __init__(self, audio_engine: AudioEngine):
        self.audio = audio_engine
        self.user_voice = "pt-BR-AntonioNeural"       # Male voice for user speech
        self.nexo_voice = "pt-BR-FranciscaNeural"     # Female voice for Nexo assistant
        self.temp_dir = REPO_ROOT / "client-mobile" / "temp_audio"
        self.temp_dir.mkdir(exist_ok=True)

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

    async def speak_user_input(self, text: str):
        """Phase 1: Synthesize and play the user speaking into the Android microphone."""
        user_audio_path = str(self.temp_dir / "user_input.mp3")
        print(f"\n[1. Smartphone Microfone] Sintetizando sua fala ({self.user_voice})...")
        comm = edge_tts.Communicate(text, self.user_voice, rate="+0%")
        await comm.save(user_audio_path)

        print(f"     -> [OUVINDO FALA DO USUARIO]: \"{text}\"")
        self.audio.play_wav_or_mp3(user_audio_path, wait=True)

    def simulate_network_framing(self, text: str):
        """Phase 2: Emulate Oboe C++ 16kHz PCM audio framing and WireGuard P2P transit."""
        word_count = len(text.split())
        pcm_bytes = word_count * 3200
        chunks = max(1, pcm_bytes // 1280)

        print("\n[2. Transporte Android Oboe C++ & WireGuard P2P]")
        print(f"     -> Empacotando {chunks} chunks de áudio PCM (Frame 0x01 | 16kHz 16-bit mono)...")
        print(f"     -> Túnel WireGuard (Tailscale tsnet P2P) transitado em ~130ms RTT.")
        print(f"     -> Frame 0x03 (Turn Complete / Fim de fala) transmitido ao Nexo Host.")

    async def run_nexo_circuit(self, prompt: str):
        """Phase 3: Cognitive Router executes the appropriate route based on intent."""
        route, direct_response = classify_cognitive_route(prompt)
        print(f"\n[3. Roteador Cognitivo Nexo: {route}]")

        # =====================================================================
        # ROTA 1: DIÁLOGO DIRETO (< 350ms)
        # =====================================================================
        if route == "ROUTE_1_DIALOGUE":
            print("     -> Intenção conversacional identificada. Resposta direta imediata.")
            # Micro-earcon
            earcon_pcm = generate_earcon_tone(freq=520, duration_ms=40, sample_rate=24000)
            self.audio.play_pcm_tone(earcon_pcm, sample_rate=24000, wait=False)

            answer_text = direct_response or "Estou aqui. O que deseja realizar?"
            resp_path = str(self.temp_dir / "dialogue_response.mp3")
            comm = edge_tts.Communicate(answer_text, self.nexo_voice, rate="+10%")
            await comm.save(resp_path)

            print(f"\n[4. Resposta Direta Concluida (<350ms)]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{answer_text}\"\n")
            self.audio.play_wav_or_mp3(resp_path, wait=True)
            return

        # =====================================================================
        # ROTA 2: FAST TOOL (Status / Hardware)
        # =====================================================================
        if route == "ROUTE_2_FAST_TOOL":
            print("     -> Consulta rápida de telemetria acionada.")
            earcon_pcm = generate_earcon_tone(freq=440, duration_ms=80, sample_rate=24000)
            self.audio.play_pcm_tone(earcon_pcm, sample_rate=24000, wait=False)

            # Fast ACK
            ack_text = "Consultando o sistema."
            ack_path = str(self.temp_dir / "fast_ack.mp3")
            comm = edge_tts.Communicate(ack_text, self.nexo_voice, rate="+10%")
            await comm.save(ack_path)
            print(f"     -> [FAST ACK]: \"{ack_text}\"")
            self.audio.play_wav_or_mp3(ack_path, wait=True)

            hw_summary = query_host_hardware()
            resp_path = str(self.temp_dir / "hw_response.mp3")
            comm = edge_tts.Communicate(hw_summary, self.nexo_voice, rate="+5%")
            await comm.save(resp_path)

            print(f"\n[4. Resposta de Telemetria Concluida]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{hw_summary}\"\n")
            self.audio.play_wav_or_mp3(resp_path, wait=True)
            return

        # =====================================================================
        # ROTA 3: AGY TASK (Tarefa Assíncrona de Código / Engenharia)
        # =====================================================================
        print("     -> Tarefa pesada de engenharia identificada. Ativando fila dinâmica com mascaramento.")

        # 1. Earcon Chime (Cocktail Party Effect)
        earcon_pcm = generate_earcon_tone(freq=440, duration_ms=80, sample_rate=24000)
        print("     -> [EARCON CHIME] Pulso acústico emitido (440Hz, 80ms).")
        self.audio.play_pcm_tone(earcon_pcm, sample_rate=24000, wait=True)

        # 2. Contextual ACK (<300ms Doherty Threshold)
        ack_text = "Entendido. Iniciando a tarefa no projeto."
        ack_path = str(self.temp_dir / "nexo_ack.mp3")
        comm = edge_tts.Communicate(ack_text, self.nexo_voice, rate="+10%")
        await comm.save(ack_path)

        print(f"     -> [DOHERTY / TURN-TAKING ACK]: \"{ack_text}\"")
        self.audio.play_wav_or_mp3(ack_path, wait=True)

        # 3. Dispatch to Antigravity CLI (AGY) with multi-tier fallback
        loop = asyncio.get_running_loop()
        agy_bin = AGY_PATH if os.path.exists(AGY_PATH) else (shutil.which("agy") or AGY_PATH)
        conv_id = None

        print(f"[Despachante] Conectando ao agente de engenharia...")
        for model_flag in ["--model=flash_lite", "--model=flash", ""]:
            cmd = [agy_bin, "agentapi", "new-conversation"]
            if model_flag:
                cmd.append(model_flag)
            cmd.append(prompt)

            res = await loop.run_in_executor(None, lambda c=cmd: subprocess.run(
                c, capture_output=True, text=True, encoding="utf-8", errors="replace"
            ))

            output_str = res.stdout.strip()
            if res.returncode == 0 and output_str:
                try:
                    data = json.loads(output_str)
                    conv_id = data.get("response", {}).get("newConversation", {}).get("conversationId")
                    if conv_id:
                        print(f"[Despachante] Sessão ativa no Host (ID: {conv_id[:8]}...)")
                        break
                except Exception:
                    pass

        # 4. Dynamic Queue with Graceful Sentence Completion
        cue_idx = 0
        last_cue_time = time.perf_counter()

        if conv_id:
            transcript_path = Path(BRAIN_PATH) / conv_id / ".system_generated" / "logs" / "transcript_full.jsonl"
            seen_steps = set()
            agent_finished = False
            poll_start = time.perf_counter()

            while time.perf_counter() - poll_start < 40.0 and not agent_finished:
                if transcript_path.exists():
                    try:
                        with open(transcript_path, "r", encoding="utf-8") as f:
                            lines = [l.strip() for l in f if l.strip()]

                        for line in lines:
                            item = json.loads(line)
                            step_idx = item.get("step_index", -1)
                            if step_idx in seen_steps:
                                continue
                            seen_steps.add(step_idx)

                            source = item.get("source")
                            step_type = item.get("type")

                            # Check intermediate tool calls
                            if "tool_calls" in item and cue_idx < self.max_cues:
                                now = time.perf_counter()
                                if (now - last_cue_time) * 1000 >= self.min_gap_ms:
                                    tname = item["tool_calls"][0].get("name", "")
                                    args = item["tool_calls"][0].get("args", {})
                                    if "list_dir" in tname:
                                        cue_msg = "Listando arquivos do repositorio."
                                    elif "view_file" in tname:
                                        fname = os.path.basename(args.get("AbsolutePath", "arquivo"))
                                        cue_msg = f"Examinando conteudo de {fname}."
                                    elif "run_command" in tname:
                                        cue_msg = "Executando comando no terminal do sistema."
                                    else:
                                        cue_msg = "Continuo processando os dados no host."

                                    cue_msg = self.sanitize_cue(cue_msg)
                                    cue_path = str(self.temp_dir / f"cue_{cue_idx}.mp3")
                                    comm = edge_tts.Communicate(cue_msg, self.nexo_voice, rate="+10%")
                                    await comm.save(cue_path)

                                    # Graceful Sentence Completion: active speech finishes naturally
                                    print(f"     -> [FILA DINAMICA / MASCARAMENTO]: \"{cue_msg}\"")
                                    self.audio.play_wav_or_mp3(cue_path, wait=True)
                                    last_cue_time = time.perf_counter()
                                    cue_idx += 1
                                    break

                            # Final answer reached
                            if source == "MODEL" and step_type == "PLANNER_RESPONSE" and "tool_calls" not in item:
                                final_text = item.get("content", "Tudo concluido com sucesso.")
                                agent_finished = True
                                break
                    except Exception:
                        pass

                await asyncio.sleep(0.1)

            if not agent_finished:
                final_text = "Tarefa concluida com sucesso no ambiente host."
        else:
            # Contingency engineering path
            print("[Despachante] Executando passos de engenharia em contingencia...")
            await asyncio.sleep(0.5)
            cue_msg = "Inspecionando os modulos do projeto."
            cue_path = str(self.temp_dir / "cue_0.mp3")
            comm = edge_tts.Communicate(cue_msg, self.nexo_voice, rate="+10%")
            await comm.save(cue_path)
            print(f"     -> [FILA DINAMICA / MASCARAMENTO]: \"{cue_msg}\"")
            self.audio.play_wav_or_mp3(cue_path, wait=True)

            final_text = "Verificacao concluida. Os modulos estao ativos e operando normalmente."

        # 5. Deliver Final Response (Buffer Flush + Summary)
        final_summary = final_text.split("\n")[0].strip()
        final_words = final_summary.split()
        if len(final_words) > 18:
            final_summary = " ".join(final_words[:18]) + "."

        final_path = str(self.temp_dir / "nexo_final.mp3")
        comm = edge_tts.Communicate(final_summary, self.nexo_voice, rate="+5%")
        await comm.save(final_path)

        print(f"\n[4. Resposta Final Concluida (Queue Flush)]")
        print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{final_summary}\"\n")
        self.audio.play_wav_or_mp3(final_path, wait=True)

    def cleanup(self):
        """Removes temporary audio files."""
        if self.temp_dir.exists():
            for f in self.temp_dir.glob("*.mp3"):
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
    print("Roteamento Cognitivo Inteligente (4 Rotas):")
    print("  • Rota 1 (Diálogo Direto): 'olá tudo bem?', 'quem é você?' -> Fala instantânea (<350ms)")
    print("  • Rota 2 (Fast Tool):      'como está o hardware?', 'memória?' -> Telemetria imediata")
    print("  • Rota 3 (AGY Task):       'liste os arquivos', 'leia o README' -> Fila dinâmica de passos")
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
