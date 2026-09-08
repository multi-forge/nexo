#!/usr/bin/env python3
"""
Nexo Dynamic Voice Queue (voice_queue.py)
========================================
Dynamic audio queue implementing psychology-informed latency masking:
- Doherty Threshold (<400ms) immediate feedback
- Conversational turn-taking pacing (<300ms ACK)
- Anti-chatter fatigue debouncing (3000ms minimum inter-cue gap)
- Working memory chunking (max 12 words per cue, max 5 cues per task)
- Queue flush on final response delivery
- Full barge-in interruption handling
"""

import os
import sys
import json
import time
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import edge_tts

# Default path locations
AGY_PATH = os.environ.get("AGY_PATH", r"C:\Users\Aluno\AppData\Local\agy\bin\agy.exe")
BRAIN_PATH = os.environ.get("BRAIN_PATH", r"C:\Users\Aluno\.gemini\antigravity-cli\brain")
CONFIG_PATH = Path(__file__).parent / "voice_ux_profile.toml"


class VoiceQueueConfig:
    def __init__(self, config_path: Optional[Path] = None):
        self.ack_deadline_ms = 300
        self.min_gap_ms = 3000
        self.max_cues = 5
        self.max_words = 12
        self.flush_on_finish = True
        self.voice_model = "pt-BR-FranciscaNeural"
        self.speaking_rate = "+10%"

        if config_path and config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
                lat = data.get("latency", {})
                q = data.get("queue", {})
                self.ack_deadline_ms = lat.get("ack_deadline_ms", self.ack_deadline_ms)
                self.min_gap_ms = q.get("min_gap_between_cues_ms", self.min_gap_ms)
                self.max_cues = q.get("max_cues_per_task", self.max_cues)
                self.max_words = q.get("max_cue_words", self.max_words)
                self.flush_on_finish = q.get("flush_on_final_response", self.flush_on_finish)
            except Exception as e:
                print(f"[VoiceQueue] Aviso: Erro ao carregar {config_path}: {e}. Usando padroes.")


class DynamicAudioQueue:
    def __init__(self, config: VoiceQueueConfig):
        self.config = config
        self.queue = asyncio.PriorityQueue()
        self.running = True
        self.timeline = []
        self.last_audio_end = time.perf_counter()
        self.max_silence_gap = 0.0
        self.cue_counter = 0
        self.t0 = time.perf_counter()
        self.interrupted = False

    async def push_cue(self, text: str, priority: int = 10):
        if self.cue_counter >= self.config.max_cues and priority >= 10:
            return  # Anti-fatigue ceiling reached for intermediate cues

        words = text.split()
        if len(words) > self.config.max_words:
            text = " ".join(words[:self.config.max_words]) + "..."

        now = time.perf_counter()
        self.cue_counter += 1
        item = (priority, self.cue_counter, {"text": text, "enqueued_at": now})
        await self.queue.put(item)
        print(f"  [QUEUE ENQUEUE] (+{(now-self.t0)*1000:6.1f}ms) -> \"{text}\"")

    def flush(self):
        """Discards intermediate queued cues when final response or interruption occurs."""
        dropped = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped > 0:
            print(f"  [QUEUE FLUSH] {dropped} mensagens intermediarias descartadas.")

    def trigger_barge_in(self):
        """Handles user interruption: immediately flushes queue and sets cancel flag."""
        self.interrupted = True
        self.flush()
        print("  [BARGE-IN] Interrupcao do usuario detectada. Audio cortado.")

    async def player_worker(self):
        while self.running or not self.queue.empty():
            try:
                priority, _, item = await asyncio.wait_for(self.queue.get(), timeout=0.1)
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
                comm = edge_tts.Communicate(text, self.config.voice_model, rate=self.config.speaking_rate)
                await comm.save(audio_path)
                word_count = len(text.split())
                speech_duration = 0.35 + (word_count * 0.11)
                await asyncio.sleep(speech_duration)
            except Exception:
                await asyncio.sleep(0.8)

            t_end = time.perf_counter()
            self.last_audio_end = t_end
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

    player_task = asyncio.create_task(queue.player_worker())

    # 1. Immediate confirmation (ACK cue < 300ms)
    await queue.push_cue("Entendido. Ja estou iniciando a tarefa solicitada.", priority=1)

    # 2. Dispatch task to Antigravity CLI via agentapi
    loop = asyncio.get_running_loop()
    print("[Despachante] Criando sessao de engenharia no agy agentapi...")
    res = await loop.run_in_executor(None, lambda: subprocess.run(
        [AGY_PATH, "agentapi", "new-conversation", "--model=flash_lite", prompt],
        capture_output=True, text=True
    ))

    if res.returncode != 0:
        print("[Despachante] Erro ao despachar agy:", res.stderr)
        queue.running = False
        await player_task
        return

    data = json.loads(res.stdout)
    conv_id = data["response"]["newConversation"]["conversationId"]
    print(f"[Despachante] Sessao ativa. ID: {conv_id}")
    await queue.push_cue("Conectado ao ambiente de execucao.", priority=5)

    transcript_path = os.path.join(BRAIN_PATH, conv_id, ".system_generated", "logs", "transcript_full.jsonl")

    # 3. Real-time tail loop with anti-fatigue debounce
    seen_steps = set()
    final_response = ""
    agent_finished = False
    last_cue_time = time.perf_counter()

    poll_start = time.perf_counter()
    while time.perf_counter() - poll_start < 60.0 and not agent_finished:
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

                    # Check tool calls
                    if "tool_calls" in item:
                        now = time.perf_counter()
                        # Apply debounce constraint: at least 3 seconds between intermediate cues
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
                                elif "write_to_file" in tname or "replace_file_content" in tname:
                                    await queue.push_cue("Atualizando arquivos de codigo.")
                                else:
                                    await queue.push_cue("Executando proxima operacao.")
                                last_cue_time = now
                                break

                    # Check final response
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
