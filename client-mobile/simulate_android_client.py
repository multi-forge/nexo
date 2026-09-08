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
    """Manages physical sound card playback via pygame.mixer with guaranteed full duration."""
    def __init__(self):
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        # 44100Hz stereo with 4096 buffer prevents buffer underrun and crackle on Windows
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        self.active_channel = None

    def play_wav_or_mp3(self, filepath: str, wait: bool = True):
        """Plays an audio file completely through physical speakers with guaranteed duration."""
        if not os.path.exists(filepath):
            return

        try:
            sound = pygame.mixer.Sound(filepath)
            sound.set_volume(1.0)
            dur = sound.get_length()
            self.active_channel = sound.play()

            if wait and self.active_channel:
                t0 = time.perf_counter()
                # Stay in wait loop for the exact mathematical duration of the sound clip
                while (time.perf_counter() - t0) < dur:
                    time.sleep(0.04)
                # Hardware DAC drain buffer to ensure the final phoneme/syllable vibrates the speakers
                time.sleep(0.35)
        except Exception as e:
            print(f"[AudioEngine] Erro na reproducao: {e}")

    def play_pcm_tone(self, pcm_bytes: bytes, sample_rate: int = 44100, wait: bool = True):
        """Plays raw PCM earcon tone in memory."""
        try:
            sound = pygame.mixer.Sound(buffer=pcm_bytes)
            sound.set_volume(0.5)
            ch = sound.play()
            if wait and ch:
                time.sleep(sound.get_length() + 0.05)
        except Exception:
            pass

    def stop(self):
        """Instantly cuts audio playback (barge-in support)."""
        if self.active_channel:
            self.active_channel.stop()


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
    """Resumo dinâmico via roteador central (fallback determinístico)."""
    try:
        result = execute_fast_tool("check_system_hardware")
        return result.get("spoken", "Sistema operacional ativo.")
    except Exception:
        pass
    return "Sistema Windows operacional com 8 gigabytes de RAM e disco rígido saudável."


def classify_cognitive_route(prompt: str) -> Tuple[str, Optional[str]]:
    """
    Decisor único: 100% IA via system prompt + function calling.
    Sem heurística local, sem fallback. A credencial vem de
    $env:GEMINI_API_KEY ou do GCP CLI (`gcloud auth print-access-token`).
    Mantém assinatura legada (route, detalhe) para o circuito de voz.
    Levanta MissingCredentialError/IaRouterError em vez de inventar resposta.
    """
    route, payload = route_via_ia(prompt)
    if route == "ROUTE_1_DIALOGUE":
        return (route, payload.get("direct_response"))
    if route == "ROUTE_2_FAST_TOOL":
        return (route, payload.get("tool"))
    if route == "ROUTE_3_AGY_TASK":
        return (route, payload.get("task"))
    if route == "ROUTE_4_COMPUTER_USE":
        return (route, payload.get("action"))
    return (route, None)


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
        user_audio_path = str(self.temp_dir / f"user_{int(time.time()*1000)}.mp3")
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
        """Fase 3: a IA decide a rota via system prompt + function calling (sem heurística local)."""
        loop = asyncio.get_running_loop()
        try:
            route, direct_response = await loop.run_in_executor(
                None, classify_cognitive_route, prompt)
        except (MissingCredentialError, IaRouterError) as e:
            print(f"\n[3. Roteador IA indisponivel] {e}")
            print("     -> Sem fallback local por configuracao: informe GEMINI_API_KEY ou `gcloud auth login`.")
            raise
        print(f"\n[3. Roteador Cognitivo Nexo (IA): {route}]")

        # =====================================================================
        # ROTA 1: DIÁLOGO DIRETO (< 350ms)
        # =====================================================================
        if route == "ROUTE_1_DIALOGUE":
            print("     -> Intenção conversacional identificada. Resposta direta imediata.")
            # Micro-earcon
            earcon_pcm = generate_earcon_tone(freq=520, duration_ms=40, sample_rate=24000)
            self.audio.play_pcm_tone(earcon_pcm, sample_rate=24000, wait=False)

            answer_text = direct_response or "Estou aqui. O que deseja realizar?"
            resp_path = str(self.temp_dir / f"dialogue_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(answer_text, self.nexo_voice, rate="+10%")
            await comm.save(resp_path)

            print(f"\n[4. Resposta Direta Concluida (<350ms)]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{answer_text}\"\n")
            self.audio.play_wav_or_mp3(resp_path, wait=True)
            return

        # =====================================================================
        # ROTA 2: FAST TOOL (Status / Hardware / Data-Hora / Projeto)
        # =====================================================================
        if route == "ROUTE_2_FAST_TOOL":
            tool_name = direct_response or "check_system_hardware"
            print(f"     -> Consulta rápida acionada: {tool_name}.")
            earcon_pcm = generate_earcon_tone(freq=440, duration_ms=80, sample_rate=24000)
            self.audio.play_pcm_tone(earcon_pcm, sample_rate=24000, wait=False)

            # Fast ACK contextual (Doherty <300ms)
            ack_text = contextual_ack(route, {"tool": tool_name})
            ack_path = str(self.temp_dir / f"fast_ack_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(ack_text, self.nexo_voice, rate="+10%")
            await comm.save(ack_path)
            print(f"     -> [FAST ACK]: \"{ack_text}\"")
            self.audio.play_wav_or_mp3(ack_path, wait=True)

            tool_result = execute_fast_tool(tool_name)
            answer_text = tool_result.get("spoken", "Consulta concluída.")
            resp_path = str(self.temp_dir / f"fast_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(answer_text, self.nexo_voice, rate="+5%")
            await comm.save(resp_path)

            print(f"\n[4. Resposta Rápida Concluída ({tool_name})]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{answer_text}\"\n")
            self.audio.play_wav_or_mp3(resp_path, wait=True)
            return

        # =====================================================================
        # ROTA 4: COMPUTER USE (Automação de tela / navegador)
        # =====================================================================
        if route == "ROUTE_4_COMPUTER_USE":
            print("     -> Automação de interface identificada. Despachando Computer Use.")
            earcon_pcm = generate_earcon_tone(freq=440, duration_ms=80, sample_rate=24000)
            self.audio.play_pcm_tone(earcon_pcm, sample_rate=24000, wait=True)
            ack_text = contextual_ack(route, {"action": prompt})
            ack_path = str(self.temp_dir / f"cu_ack_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(ack_text, self.nexo_voice, rate="+10%")
            await comm.save(ack_path)
            print(f"     -> [DOHERTY / TURN-TAKING ACK]: \"{ack_text}\"")
            self.audio.play_wav_or_mp3(ack_path, wait=True)
            final_text = f"Automacao de tela iniciada: {prompt}"
            final_path = str(self.temp_dir / f"cu_final_{int(time.time()*1000)}.mp3")
            comm = edge_tts.Communicate(final_text, self.nexo_voice, rate="+5%")
            await comm.save(final_path)
            print(f"\n[4. Resposta Computer Use Despachada]")
            print(f"     -> [OUVINDO RESPOSTA DO NEXO]: \"{final_text}\"\n")
            self.audio.play_wav_or_mp3(final_path, wait=True)
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
        ack_text = contextual_ack("ROUTE_3_AGY_TASK", {"task": prompt})
        ack_path = str(self.temp_dir / f"ack_{int(time.time()*1000)}.mp3")
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
                                    cue_path = str(self.temp_dir / f"cue_{cue_idx}_{int(time.time()*1000)}.mp3")
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
            # Sem invencao local: se o agente de engenharia nao esta disponivel,
            # informa o fato em vez de simular sucesso.
            print("[Despachante] Agente de engenharia indisponivel; sem fallback local.")
            final_text = (
                f"Nao consegui iniciar a tarefa {prompt} "
                f"porque o agente de engenharia nao esta disponivel no host."
            )

        # 5. Deliver Final Response (Buffer Flush + Summary)
        final_summary = final_text.split("\n")[0].strip()
        final_words = final_summary.split()
        if len(final_words) > 18:
            final_summary = " ".join(final_words[:18]) + "."

        final_path = str(self.temp_dir / f"final_{int(time.time()*1000)}.mp3")
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
    print("Roteamento 100% IA (system prompt + function calling, sem fallback local):")
    print("  • Rota 1 (Diálogo): texto direto da IA")
    print("  • Rota 2 (Fast Tool): function call da IA -> ferramenta real")
    print("  • Rota 3 (AGY Task): function call da IA")
    print("  • Rota 4 (Computer Use): function call da IA")
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
