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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ia_router import (
    SYSTEM_PROMPT as IA_SYSTEM_PROMPT,
    TOOLS_DECLARATION as IA_TOOLS,
    resolve_gemini_credential,
    MissingCredentialError,
)

DAEMON_EXE = os.environ.get(
    "NEXO_DAEMON_EXE",
    r"C:\Users\Aluno\nexo-daemon\target\release\nexo-daemon.exe"
)
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "models/gemini-2.5-flash-native-audio-latest")


def _live_api_key() -> str:
    """API key via env ou GCP CLI. Sem fallback silencioso."""
    kind, credential = resolve_gemini_credential()
    if kind == "apikey":
        return credential
    # Live WebSocket exige API key; token Vertex (OAuth) nao autentica este endpoint.
    raise MissingCredentialError(
        "Gemini Live exige $env:GEMINI_API_KEY (o token do GCP CLI serve ao "
        "roteador REST/Vertex, nao ao WebSocket Live). Defina a API key."
    )


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


def query_system_datetime() -> Dict[str, Any]:
    """Data/hora real do host (fast tool, sem LLM)."""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cognitive_router import get_system_datetime
    return get_system_datetime()


def query_project_status() -> Dict[str, Any]:
    """Listagem rápida real do projeto (fast tool, sem AGY)."""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cognitive_router import list_project_files
    return list_project_files()


async def run_live_session(input_pcm_stream: Optional[bytes] = None, output_wav_path: Optional[str] = None):
    try:
        gemini_api_key = _live_api_key()
    except MissingCredentialError as e:
        print(f"[LiveBridge] Erro: {e}")
        sys.exit(1)

    ws_url = (
        f"wss://generativelanguage.googleapis.com/ws/"
        f"google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
        f"?key={gemini_api_key}"
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
                "tools": IA_TOOLS,
                "systemInstruction": {
                    "parts": [{
                        "text": IA_SYSTEM_PROMPT
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
                        elif fn_name == "get_system_datetime":
                            dt_data = query_system_datetime()
                            fn_responses.append({
                                "id": call_id,
                                "name": fn_name,
                                "response": {"result": dt_data}
                            })
                        elif fn_name == "list_project_files":
                            proj_data = query_project_status()
                            fn_responses.append({
                                "id": call_id,
                                "name": fn_name,
                                "response": {"result": proj_data}
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
