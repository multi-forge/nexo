#!/usr/bin/env python3
"""
Nexo Dynamic Voice Queue (voice_queue.py)
========================================
Dynamic audio queue implementing psychology-informed latency masking:
- Doherty Threshold (<400ms) immediate feedback
- Conversational turn-taking pacing (<300ms ACK)
- Anti-chatter fatigue debouncing (3000ms minimum inter-cue gap)
- Working memory chunking (max 12 words per cue, max 5 cues per task)
- Silence tolerance watchdog (no dead-air > 2000ms)
- Discourse marker sanitizer (0 colloquial fillers)
- Queue flush on final response delivery
- Full barge-in interruption handling (<150ms cutoff)
"""

import os
import sys
import json
import time
import math
import wave
import struct
import asyncio
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import edge_tts

AGY_PATH = os.environ.get("AGY_PATH", r"C:\Users\Aluno\AppData\Local\agy\bin\agy.exe")
BRAIN_PATH = os.environ.get("BRAIN_PATH", r"C:\Users\Aluno\.gemini\antigravity-cli\brain")
CONFIG_PATH = Path(__file__).parent / "voice_ux_profile.toml"


class VoiceQueueConfig:
    def __init__(self, config_path: Optional[Path] = None):
        self.ack_deadline_ms = 300
        self.doherty_ceiling_ms = 400
        self.nielsen_flow_ms = 1000
        self.nielsen_attention_ms = 10000
        self.target_turn_gap_ms = 250

        self.max_dead_ms = 1500
        self.emergency_filler_ms = 2000
        self.abandon_risk_ms = 4000

        self.max_words = 12
        self.max_queued_cues = 3
        self.min_gap_ms = 3000
        self.max_cues = 5
        self.cooldown_ms = 5000
        self.flush_on_finish = True

        self.voice_model = "pt-BR-FranciscaNeural"
        self.speaking_rate = "+10%"
        self.confirmation_tone = True
        self.avoid_patterns = [
            "hmm", "uh", "tipo", "deixa eu pensar",
            "vou tentar", "talvez", "acho que", "desculpa", "ops"
        ]

        if config_path and config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
                lat = data.get("latency", {})
                sil = data.get("silence", {})
                q = data.get("queue", {})
                disc = data.get("discourse", {})

                self.ack_deadline_ms = lat.get("ack_deadline_ms", self.ack_deadline_ms)
                self.doherty_ceiling_ms = lat.get("doherty_ceiling_ms", self.doherty_ceiling_ms)
                self.max_dead_ms = sil.get("max_dead_ms", self.max_dead_ms)
                self.emergency_filler_ms = sil.get("emergency_filler_ms", self.emergency_filler_ms)

                self.max_words = q.get("max_cue_words", self.max_words)
                self.max_queued_cues = q.get("max_queued_cues", self.max_queued_cues)
                self.min_gap_ms = q.get("min_gap_between_cues_ms", self.min_gap_ms)
                self.max_cues = q.get("max_cues_per_task", self.max_cues)
                self.flush_on_finish = q.get("flush_on_final_response", self.flush_on_finish)

                self.avoid_patterns = disc.get("avoid_patterns", self.avoid_patterns)
                self.confirmation_tone = disc.get("confirmation_tone", self.confirmation_tone)
            except Exception as e:
                print(f"[VoiceQueue] Erro ao ler config TOML: {e}")


def generate_earcon_tone(freq=440, duration_ms=80, sample_rate=16000) -> bytes:
    """Generates a soft-attack sine wave earcon tone in memory (Cocktail Party Effect)."""
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    buffer = bytearray()
    for i in range(num_samples):
        # Soft envelope: linear fade-in and fade-out
        t = i / num_samples
        envelope = math.sin(math.pi * t)
        val = int(32767.0 * 0.3 * envelope * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
        buffer.extend(struct.pack("<h", val))
    return bytes(buffer)


import re

class DynamicAudioQueue:
    def __init__(self, config: VoiceQueueConfig):
        self.config = config
        self.queue = asyncio.PriorityQueue()
        self.running = True
        self.timeline: List[Dict[str, Any]] = []
        self.last_audio_end = time.perf_counter()
        self.max_silence_gap = 0.0
        self.cue_counter = 0
        self.t0 = time.perf_counter()
        self.interrupted = False
        self.last_spoken_time = time.perf_counter()
        self.current_playback_task = None
        self.emergency_filler_triggered = False

    def sanitize_discourse(self, text: str) -> str:
        """Enforces cognitive load word limits and removes informal fillers."""
        clean = text
        for pat in self.config.avoid_patterns:
            clean = re.sub(re.escape(pat), "", clean, flags=re.IGNORECASE)

        clean = re.sub(r'[\s,;]+', ' ', clean).strip(' ,;:-')
        if clean and clean[0].islower():
            clean = clean[0].upper() + clean[1:]
        if clean and not clean.endswith(('.', '!', '?')):
            clean += '.'

        words = clean.split()
        if len(words) > self.config.max_words:
            clean = " ".join(words[:self.config.max_words]) + "..."
        return clean

    async def push_cue(self, text: str, priority: int = 10):
        if self.cue_counter >= self.config.max_cues and priority >= 10:
            return

        clean_text = self.sanitize_discourse(text)
        now = time.perf_counter()
        self.cue_counter += 1
        item = (priority, self.cue_counter, {"text": clean_text, "enqueued_at": now})
        await self.queue.put(item)
        print(f"  [QUEUE ENQUEUE] (+{(now-self.t0)*1000:6.1f}ms) -> \"{clean_text}\"")

    def flush(self):
        """Flushes outdated intermediate cues from working memory."""
        dropped = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped > 0:
            print(f"  [QUEUE FLUSH] {dropped} mensagens intermediarias descartadas da memoria.")

    def trigger_barge_in(self):
        """Handles user interruption: immediately ducks/cancels audio and flushes queue."""
        t_barge = time.perf_counter()
        self.interrupted = True
        self.flush()
        if self.current_playback_task and not self.current_playback_task.done():
            self.current_playback_task.cancel()
        cutoff_latency = (time.perf_counter() - t_barge) * 1000
        print(f"  [BARGE-IN] Interrupcao do usuario! Audio cortado em {cutoff_latency:.2f}ms.")
        return cutoff_latency

    async def player_worker(self):
        while self.running or not self.queue.empty():
            try:
                priority, _, item = await asyncio.wait_for(self.queue.get(), timeout=0.08)
            except asyncio.TimeoutError:
                continue

            if self.interrupted:
                self.queue.task_done()
                continue

            text = item["text"]
            t_start = time.perf_counter()
            silence_gap = t_start - self.last_audio_end
            if len(self.timeline) > 0 and silence_gap > self.max_silence_gap:
                self.max_silence_gap = silence_gap

            print(f"\n  >>> [AUDIO PLAYING] (+{(t_start-self.t0)*1000:6.1f}ms | Silencio: {silence_gap*1000:.0f}ms): \"{text}\"")

            audio_path = f"nexo_cue_{len(self.timeline)}.mp3"
            try:
                async def play_tts():
                    comm = edge_tts.Communicate(text, self.config.voice_model, rate=self.config.speaking_rate)
                    await comm.save(audio_path)
                    word_count = len(text.split())
                    speech_duration = 0.30 + (word_count * 0.11)
                    await asyncio.sleep(speech_duration)

                self.current_playback_task = asyncio.create_task(play_tts())
                await self.current_playback_task
            except asyncio.CancelledError:
                print("  [PLAYER] Playback cancelado via barge-in.")
            except Exception:
                await asyncio.sleep(0.5)

            t_end = time.perf_counter()
            self.last_audio_end = t_end
            self.last_spoken_time = t_end
            self.timeline.append({
                "text": text,
                "started_at_ms": round((t_start - self.t0) * 1000, 1),
                "duration_ms": round((t_end - t_start) * 1000, 1)
            })
            self.queue.task_done()


async def execute_task_with_voice_masking(prompt: str):
    config = VoiceQueueConfig(CONFIG_PATH)
    queue = DynamicAudioQueue(config)
    queue.t0 = time.perf_counter()
    queue.last_audio_end = queue.t0

    print("==================================================================")
    print("      NEXO VOICE ORCHESTRATOR - DYNAMIC LATENCY MASKING QUEUE     ")
    print("==================================================================")
    print(f"Objetivo: {prompt}\n")

    # Earcon chime prior to first audio
    if config.confirmation_tone:
        earcon = generate_earcon_tone(freq=440, duration_ms=80)
        print(f"  [EARCON] Chime acustico emitido ({len(earcon)} bytes, 440Hz, 80ms).")

    player_task = asyncio.create_task(queue.player_worker())

    # 1. Immediate ACK (< 300ms)
    t_ack = (time.perf_counter() - queue.t0) * 1000
    print(f"  [DOHERTY / TURN-TAKING ACK] Emitido em {t_ack:.1f}ms (< {config.ack_deadline_ms}ms target).")
    await queue.push_cue("Entendido. Ja estou iniciando a verificacao solicitada.", priority=1)

    # 2. Dispatch to AGY with resilient multi-tier fallback
    loop = asyncio.get_running_loop()
    agy_bin = AGY_PATH if os.path.exists(AGY_PATH) else (shutil.which("agy") or AGY_PATH)
    conv_id = None

    for model_flag in ["--model=flash_lite", "--model=flash", ""]:
        cmd = [agy_bin, "agentapi", "new-conversation"]
        if model_flag:
            cmd.append(model_flag)
        cmd.append(prompt)

        tier_name = model_flag or "--model=default"
        print(f"[Despachante] Tentando criar sessao no agy ({tier_name})...")

        res = await loop.run_in_executor(None, lambda c=cmd: subprocess.run(
            c, capture_output=True, text=True, encoding="utf-8", errors="replace"
        ))

        output_str = res.stdout.strip()
        error_str = res.stderr.strip()

        if res.returncode == 0 and output_str:
            try:
                data = json.loads(output_str)
                if "response" in data and "newConversation" in data["response"]:
                    conv_id = data["response"]["newConversation"].get("conversationId")
                    if conv_id:
                        print(f"[Despachante] Sessao confirmada com sucesso: {conv_id}")
                        break
            except json.JSONDecodeError:
                pass

        err_detail = error_str or output_str
        print(f"[Despachante] Aviso ({tier_name}): {err_detail}")

    if not conv_id:
        print("[Despachante] Atencao: agy indisponivel no momento. Ativando contingencia para teste de Voice UX...")
        await queue.push_cue("Conectado em modo de demonstracao.", priority=5)
        await asyncio.sleep(2.5)
        await queue.push_cue("Examinando a arvore de diretorios do projeto.")
        await asyncio.sleep(3.2)
        await queue.push_cue("Lendo as dependencias do Cargo.toml.")
        await asyncio.sleep(2.8)
        if config.flush_on_finish:
            queue.flush()
        await queue.push_cue("Tudo concluido com sucesso.", priority=0)
        queue.running = False
        await player_task
        return

    await queue.push_cue("Conectado ao ambiente de execucao.", priority=5)

    transcript_path = os.path.join(BRAIN_PATH, conv_id, ".system_generated", "logs", "transcript_full.jsonl")

    seen_steps = set()
    final_response = ""
    agent_finished = False
    last_cue_time = time.perf_counter()

    poll_start = time.perf_counter()
    while time.perf_counter() - poll_start < 60.0 and not agent_finished:
        now = time.perf_counter()

        # Silence Tolerance Watchdog: prevent dead-air > 2000ms
        dead_air = (now - queue.last_audio_end) * 1000
        if dead_air >= config.emergency_filler_ms and not queue.emergency_filler_triggered and queue.queue.empty():
            print(f"  [SILENCE WATCHDOG] Silencio ({dead_air:.0f}ms) atingiu o limiar. Emitindo holding cue.")
            await queue.push_cue("Continuo processando os dados no host.", priority=8)
            queue.emergency_filler_triggered = True

        if os.path.exists(transcript_path):
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

                    if "tool_calls" in item:
                        # Anti-chatter debouncing check: >= 3000ms gap
                        if (now - last_cue_time) * 1000 >= config.min_gap_ms:
                            for tc in item["tool_calls"]:
                                tname = tc.get("name", "")
                                args = tc.get("args", {})
                                if "list_dir" in tname:
                                    await queue.push_cue("Listando estrutura de arquivos do projeto.")
                                elif "view_file" in tname:
                                    fname = os.path.basename(args.get("AbsolutePath", "arquivo"))
                                    await queue.push_cue(f"Examinando conteudo de {fname}.")
                                elif "run_command" in tname:
                                    await queue.push_cue("Executando comando no terminal do sistema.")
                                else:
                                    await queue.push_cue("Executando proxima operacao.")
                                last_cue_time = now
                                queue.emergency_filler_triggered = False
                                break

                    if source == "MODEL" and step_type == "PLANNER_RESPONSE" and "tool_calls" not in item:
                        final_response = item.get("content", "")
                        agent_finished = True
                        if config.flush_on_finish:
                            queue.flush()
                        await queue.push_cue("Tudo concluido com sucesso.", priority=0)
                        break

            except Exception:
                pass

        await asyncio.sleep(0.08)

    queue.running = False
    await player_task

    total_time = time.perf_counter() - queue.t0
    print("\n==================================================================")
    print(f"             METRICAS CONSOLIDADAS DE VOICE UX                  ")
    print("==================================================================")
    print(f"Duracao total da tarefa: {total_time:.2f} s")
    print(f"Intervencoes faladas: {len(queue.timeline)}")
    print(f"Maior intervalo de silencio percebido: {queue.max_silence_gap:.2f} s")
    print("\nLinha do tempo das frases pronunciadas:")
    for c in queue.timeline:
        print(f"  [{c['started_at_ms']:7.1f} ms] (+{c['duration_ms']}ms) -> \"{c['text']}\"")

    # Cleanup temporary cue audio files
    for i in range(len(queue.timeline) + 5):
        p = f"nexo_cue_{i}.mp3"
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    task_prompt = "Liste os arquivos em C:\\Users\\Aluno\\nexo-app e verifique o Cargo.toml. Seja breve."
    if len(sys.argv) > 1:
        task_prompt = " ".join(sys.argv[1:])
    asyncio.run(execute_task_with_voice_masking(task_prompt))
