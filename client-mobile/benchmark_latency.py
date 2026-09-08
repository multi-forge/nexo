#!/usr/bin/env python3
"""
Nexo: Benchmark Completo de Computer Use e Retorno de Voz
===============================================================
- Conecta ao Windows Host via Tailscale WebSocket
- Dispara navegacao web real no Chromium em RAM
- Extrai manchete e tira screenshot
- Sintetiza voz no Gemini 3.1 Live
- Mede todas as etapas de latencia
- Salva o audio completo e screenshot na pasta Downloads
"""

import asyncio
import base64
import json
import os
import subprocess
import time
import wave
import websockets

WINDOWS_HOST_URL = "ws://100.86.250.65:8765"
DOWNLOADS = "/storage/emulated/0/Download"

async def run_cu_benchmark():
    print("════════════════════════════════════════════════════════════════════")
    print("   BENCHMARK COMPUTER USE REAL: CELULAR ➔ WINDOWS BROWSER ➔ VOZ   ")
    print("════════════════════════════════════════════════════════════════════\n")

    t0 = time.perf_counter()
    async with websockets.connect(WINDOWS_HOST_URL, max_size=10*2**20) as ws:
        t_conn = (time.perf_counter() - t0) * 1000
        print(f"[1] Conexao Celular ➔ Windows Host (Tailscale): {t_conn:.1f} ms")

        prompt = "Nexo, acesse 'https://news.ycombinator.com', tire um screenshot e me diga em uma frase curta qual a manchete principal."
        print(f"\n[Celular] Enviando comando de Computer Use:\n   \"{prompt}\"\n")
        
        t_send = time.perf_counter()
        await ws.send(prompt)

        audio_bytes = []
        first_audio_t = None
        print("[Windows] Executando Computer Use (Playwright Chromium + Visao)...")

        for i in range(50):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=25)
            except asyncio.TimeoutError:
                print("   [Timeout de recepcao]")
                break

            if isinstance(raw, bytes) and len(raw) > 1 and raw[0] == 0x02:
                if first_audio_t is None:
                    first_audio_t = time.perf_counter()
                    ttfa_ms = (first_audio_t - t_send) * 1000
                    print(f"\n[2] TIME TO FIRST AUDIO (Voz no Ouvido apos Computer Use): {ttfa_ms:.1f} ms ({ttfa_ms/1000:.2f}s)")
                audio_bytes.append(raw[1:])
            
            # Se já recebemos mais de 100KB de áudio, podemos concluir
            if len(audio_bytes) > 120000:
                break

        t_end = time.perf_counter()
        dur_total_ms = (t_end - t_send) * 1000

        if audio_bytes:
            pcm = b"".join(audio_bytes)
            dur_s = len(pcm) / (24000 * 2)
            
            print(f"[3] Duracao da fala do Nexo: {dur_s:.2f} segundos ({len(pcm):,} bytes PCM 24kHz)")
            print(f"[4] TEMPO TOTAL DA OPERACAO COMPLETA: {dur_total_ms:.1f} ms ({dur_total_ms/1000:.2f}s)")

            # Salvar WAV
            wav_path = os.path.join(DOWNLOADS, "nexo_computer_use_complete_voice.wav")
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(pcm)
            print(f"\n[Audio Completo Salvo]: {wav_path}")

            # Baixar screenshot atualizado do Windows via SCP
            print("Baixando screenshot atualizado do Windows...")
            subprocess.run([
                "scp", "-i", os.path.expanduser("~/.ssh/id_rsa_windows"),
                "-o", "StrictHostKeyChecking=no",
                "Aluno@100.86.250.65:C:/Users/Aluno/nexo_latest_screenshot.png",
                os.path.join(DOWNLOADS, "nexo_computer_use_final_screenshot.png")
            ], capture_output=True)
            print(f"[Screenshot Salvo]: {os.path.join(DOWNLOADS, 'nexo_computer_use_final_screenshot.png')}")

            print("\n════════════════════════════════════════════════════════════════════")
            print("OPERACAO COMPUTER USE + VOZ CONCLUIDA COM SUCESSO!")
            print("════════════════════════════════════════════════════════════════════\n")
        else:
            print("Nenhum audio recebido.")

if __name__ == "__main__":
    asyncio.run(run_cu_benchmark())
