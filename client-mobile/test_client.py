#!/usr/bin/env python3
"""
Jarvis-Dev: Teste de Cliente Mobile conectado ao Host Windows
=============================================================
Conecta no IP Tailscale do Windows (ws://100.86.250.65:8765)
e testa o fluxo completo:
1. Envia comando de áudio/texto
2. Host Windows aciona o Gemini 3.1 Live
3. Executa comando PowerShell no Windows
4. Tira screenshot da tela do Windows
5. Recebe voz sintetizada do Windows de volta no celular
"""

import asyncio
import base64
import json
import os
import time
import wave
import websockets

WINDOWS_HOST_URL = "ws://100.86.250.65:8765"
DOWNLOADS = "/storage/emulated/0/Download"

async def test_windows_client():
    print(f"🔗 Conectando ao Host Windows em {WINDOWS_HOST_URL}...")
    t0 = time.perf_counter()

    async with websockets.connect(WINDOWS_HOST_URL, max_size=10*2**20) as ws:
        t_conn = (time.perf_counter() - t0) * 1000
        print(f"✅ Conexão WebSocket com Windows Host estabelecida em {t_conn:.1f} ms!")

        # Enviar comando de teste
        prompt = "Jarvis, execute um comando powershell para me dizer o nome da máquina Windows e tire um screenshot da tela."
        print(f"\n👤 [Celular] Enviando prompt: \"{prompt}\"")

        # No protocolo: 0x01 para áudio, ou comando texto para teste
        # Para testar texto pelo gateway, podemos simular turno
        msg = {
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": prompt}]}],
                "turnComplete": True
            }
        }
        # Envia via frame de controle ou texto
        await ws.send(json.dumps(msg))

        audio_bytes = []
        print("🎧 Aguardando execução de ferramentas no Windows e resposta falada...")

        for i in range(30):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                break

            # Se for frame 0x02 (áudio binário do Gemini)
            if isinstance(raw, bytes) and len(raw) > 1 and raw[0] == 0x02:
                audio_bytes.append(raw[1:])
            elif isinstance(raw, str):
                print("   [Log Host]:", raw[:100])

        t_total = time.perf_counter() - t0

        if audio_bytes:
            pcm = b"".join(audio_bytes)
            dur = len(pcm) / (24000 * 2)
            print(f"\n🎉 RESPOSTA RECEBIDA DO WINDOWS!")
            print(f"⏱️  Tempo Total: {t_total:.2f}s")
            print(f"🔊 Áudio do Jarvis (Windows ➔ Celular): {len(pcm):,} bytes ({dur:.2f}s)")
            wav_path = os.path.join(DOWNLOADS, "jarvis_windows_live_response.wav")
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(pcm)
            print(f"📁 Áudio salvo em: {wav_path}")
        else:
            print("Nenhum áudio retornado do gateway.")

asyncio.run(test_windows_client())
