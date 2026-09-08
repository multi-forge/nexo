#!/usr/bin/env python3
"""
Nexo Multimodal Live Bridge (live_bridge.py)
============================================
Bidirectional WebSocket gateway between client audio stream and Google Gemini
Multimodal Live API:
- Streams raw 16kHz PCM audio to Gemini Live
- Receives 24kHz PCM audio responses (Aoede voice)
- Dispatches native Function Calls directly to Nexo Daemon (Win32/HostRuntime)
- Zero hardcoded credentials (strictly environment variable based)
"""

import os
import sys
import json
import ssl
import wave
import base64
import time
import asyncio
import subprocess
from typing import Optional, Dict, Any
import websockets

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DAEMON_EXE = os.environ.get(
    "NEXO_DAEMON_EXE",
    r"C:\Users\Aluno\nexo-daemon\target\release\nexo-daemon.exe"
)
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "models/gemini-2.5-flash-native-audio-latest")


def query_host_hardware() -> Dict[str, Any]:
    """Queries hardware telemetry from Nexo Daemon binary or falls back to system info."""
    if os.path.exists(DAEMON_EXE):
        try:
            res = subprocess.run([DAEMON_EXE, "hardware"], capture_output=True, text=True, check=True, timeout=5)
            return json.loads(res.stdout.strip())
        except Exception as e:
            print(f"[LiveBridge] Aviso ao consultar nexo-daemon: {e}")

    # Fallback inspection
    return {
        "status": "online",
        "platform": sys.platform,
        "note": "Hardware runtime query direct fallback"
    }


async def run_live_session(input_pcm_stream: Optional[bytes] = None, output_wav_path: Optional[str] = None):
    if not GEMINI_API_KEY:
        print("[LiveBridge] Erro: GEMINI_API_KEY nao definida no ambiente.")
        sys.exit(1)

    ws_url = (
        f"wss://generativelanguage.googleapis.com/ws/"
        f"google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
        f"?key={GEMINI_API_KEY}"
    )

    print("==================================================================")
    print("      NEXO MULTIMODAL LIVE BRIDGE - GEMINI WEBSOCKET RELAY        ")
    print("==================================================================")

    ctx = ssl.create_default_context()
    async with websockets.connect(ws_url, ssl=ctx, max_size=10 * 2**20) as ws:
        setup_msg = {
            "setup": {
                "model": LIVE_MODEL,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": "Aoede"}
                        }
                    }
                },
                "tools": [{
                    "functionDeclarations": [
                        {
                            "name": "check_system_hardware",
                            "description": "Consulta telemetria e inventario de hardware da maquina fisica (CPU, memoria, GPU, discos).",
                            "parameters": {"type": "OBJECT", "properties": {}}
                        }
                    ]
                }],
                "systemInstruction": {
                    "parts": [{
                        "text": (
                            "Voce e o assistente de voz do Nexo. "
                            "Responda de forma concisa e natural em portugues do Brasil por audio. "
                            "Quando solicitado para checar hardware ou sistema, invoque check_system_hardware."
                        )
                    }]
                }
            }
        }

        await ws.send(json.dumps(setup_msg))
        init_resp = await ws.recv()
        print("[LiveBridge] Handshake com Gemini Live confirmado.")

        if input_pcm_stream:
            chunk_size = 1280  # 40ms at 16kHz 16-bit mono
            for i in range(0, len(input_pcm_stream), chunk_size):
                chunk = input_pcm_stream[i:i + chunk_size]
                msg = {
                    "realtimeInput": {
                        "mediaChunks": [{
                            "mimeType": "audio/pcm",
                            "data": base64.b64encode(chunk).decode("utf-8")
                        }]
                    }
                }
                await ws.send(json.dumps(msg))
                await asyncio.sleep(0.02)

        received_audio = bytearray()
        t0 = time.perf_counter()

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=12.0)
                data = json.loads(raw)

                if "toolCall" in data:
                    tc = data["toolCall"]
                    fn_calls = tc.get("functionCalls", [])
                    fn_responses = []
                    for fc in fn_calls:
                        fn_name = fc.get("name")
                        call_id = fc.get("id")
                        print(f"[LiveBridge] Executando tool call: {fn_name}")
                        if fn_name == "check_system_hardware":
                            hw_data = query_host_hardware()
                            fn_responses.append({
                                "id": call_id,
                                "name": fn_name,
                                "response": {"result": hw_data}
                            })

                    await ws.send(json.dumps({
                        "toolResponse": {"functionResponses": fn_responses}
                    }))
                    continue

                if "serverContent" in data:
                    sc = data["serverContent"]
                    if "modelTurn" in sc:
                        for p in sc["modelTurn"].get("parts", []):
                            if "inlineData" in p:
                                pcm = base64.b64decode(p["inlineData"]["data"])
                                received_audio.extend(pcm)
                    if sc.get("turnComplete"):
                        print(f"[LiveBridge] Resposta final recebida: {len(received_audio)} bytes PCM 24kHz.")
                        break

            except asyncio.TimeoutError:
                break

        if output_wav_path and len(received_audio) > 0:
            with wave.open(output_wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(received_audio)
            print(f"[LiveBridge] Audio salvo em: {output_wav_path}")

        return received_audio


if __name__ == "__main__":
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with wave.open(sys.argv[1], "rb") as wav:
            pcm_in = wav.readframes(wav.getnframes())
            # Add padding silence for VAD trigger
            pcm_in += b"\x00" * 32000
    else:
        pcm_in = None

    asyncio.run(run_live_session(pcm_in, "nexo_live_response.wav"))
