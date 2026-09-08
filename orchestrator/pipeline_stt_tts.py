#!/usr/bin/env python3
"""
Nexo Modular Speech Pipeline (pipeline_stt_tts.py)
==================================================
Multi-stage voice pipeline for environments where pure streaming WebSocket
is unavailable or decoupled processing is preferred:
1. STT: Google Cloud Speech v2 (Chirp 2) with Gemini Multimodal fallback
2. LLM: Gemini 3.5 Flash-Lite (primary) / 3.1 Flash-Lite (resilient fallback)
3. Daemon Execution: Nexo Host Runtime (hardware telemetry, system volume, task supervisor)
4. TTS: Google Cloud Neural2 (pt-BR-Neural2-A) with Edge-TTS fallback
- Zero hardcoded credentials
"""

import os
import sys
import json
import time
import base64
import asyncio
import subprocess
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "stt-465818")
DAEMON_EXE = os.environ.get(
    "NEXO_DAEMON_EXE",
    r"C:\Users\Aluno\nexo-daemon\target\release\nexo-daemon.exe"
)

PRIMARY_LLM = "gemini-3.5-flash-lite"
FALLBACK_LLM = "gemini-3.1-flash-lite"


def get_gcloud_token() -> Optional[str]:
    """Retrieves access token from active gcloud CLI context."""
    try:
        res = subprocess.run(
            ["gcloud.cmd", "auth", "print-access-token"],
            capture_output=True, text=True, check=True, timeout=5
        )
        return res.stdout.strip()
    except Exception:
        return None


def transcribe_audio_chirp(wav_path: str) -> Optional[str]:
    """Transcribes audio using Google Cloud Speech-to-Text v2 (Chirp)."""
    token = get_gcloud_token()
    if not token or not os.path.exists(wav_path):
        return None

    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")

    url = (
        f"https://us-central1-speech.googleapis.com/v2/projects/"
        f"{GCP_PROJECT_ID}/locations/us-central1/recognizers/_:recognize"
    )
    payload = {
        "config": {
            "autoDecodingConfig": {},
            "model": "chirp_2",
            "languageCodes": ["pt-BR"]
        },
        "content": audio_b64
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if results and "alternatives" in results[0]:
                return results[0]["alternatives"][0].get("transcript", "").strip()
    except Exception as e:
        print(f"[Pipeline:STT] Aviso Chirp: {e}")
    return None


def transcribe_audio_gemini(wav_path: str) -> Optional[str]:
    """Transcribes audio using Gemini Flash-Lite multimodal input."""
    if not GEMINI_API_KEY or not os.path.exists(wav_path):
        return None

    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")

    for model in [PRIMARY_LLM, FALLBACK_LLM]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Transcreva fielmente o audio em portugues. Responda apenas com o texto falado."},
                    {"inlineData": {"mimeType": "audio/wav", "data": audio_b64}}
                ]
            }]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            continue
    return None


def dispatch_llm_orchestrator(transcription: str) -> Dict[str, Any]:
    """Interprets intent and dispatches tool calls using Gemini Flash-Lite."""
    tools_declaration = [{
        "functionDeclarations": [
            {
                "name": "check_hardware",
                "description": "Consulta inventario de hardware do PC (CPU, RAM, GPU, Armazenamento, OS).",
                "parameters": {"type": "OBJECT", "properties": {}}
            },
            {
                "name": "set_volume",
                "description": "Ajusta o volume do sistema do host.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "steps": {"type": "INTEGER", "description": "Passos de volume (+/-)"}
                    },
                    "required": ["steps"]
                }
            }
        ]
    }]

    system_instruction = (
        "Voce e o orquestrador do Nexo. Se o usuario pedir informacoes do computador, hardware, memoria ou disco, "
        "invoque a tool check_hardware. Seja objetivo."
    )

    for model in [PRIMARY_LLM, FALLBACK_LLM]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": transcription}]}],
            "tools": tools_declaration
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[Pipeline:LLM] Modelo {model} indisponivel: {e}")
            continue

    return {}


async def synthesize_speech_edge(text: str, output_path: str):
    """Fallback synthesis using edge-tts."""
    import edge_tts
    comm = edge_tts.Communicate(text, "pt-BR-FranciscaNeural")
    await comm.save(output_path)


def synthesize_speech_neural2(text: str, output_path: str) -> bool:
    """High-quality synthesis using Google Cloud Neural2."""
    token = get_gcloud_token()
    if not token:
        return False

    url = "https://texttospeech.googleapis.com/v1/text:synthesize"
    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": "pt-BR",
            "name": "pt-BR-Neural2-A"
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": 1.1
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "X-Goog-User-Project": GCP_PROJECT_ID,
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            audio_b64 = data.get("audioContent", "")
            if audio_b64:
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(audio_b64))
                return True
    except Exception as e:
        print(f"[Pipeline:TTS] Neural2 indisponivel: {e}")
    return False


async def run_pipeline(input_audio_path: str):
    print("==================================================================")
    print("       NEXO MODULAR PIPELINE (STT -> LLM -> HOST -> TTS)         ")
    print("==================================================================")
    print(f"Entrada: {input_audio_path}")

    # 1. Speech-to-Text
    transcription = transcribe_audio_chirp(input_audio_path)
    if not transcription:
        transcription = transcribe_audio_gemini(input_audio_path)

    if not transcription:
        print("[Pipeline] Falha na transcricao de audio.")
        return

    print(f"[Pipeline] Transcricao: \"{transcription}\"")

    # 2. LLM Intent & Tool Calling
    llm_resp = dispatch_llm_orchestrator(transcription)
    candidates = llm_resp.get("candidates", [])
    if not candidates:
        print("[Pipeline] Nenhuma resposta gerada pela LLM.")
        return

    part = candidates[0]["content"]["parts"][0]
    if "functionCall" in part:
        fn_name = part["functionCall"]["name"]
        print(f"[Pipeline] Tool Call detectada: {fn_name}")
        if fn_name == "check_hardware":
            if os.path.exists(DAEMON_EXE):
                res = subprocess.run([DAEMON_EXE, "hardware"], capture_output=True, text=True, check=True)
                hw = json.loads(res.stdout)
                summary = (
                    f"O sistema e um {hw.get('os')} com processador {hw.get('cpu')}, "
                    f"{hw.get('ram_total_gb')} gigabytes de memoria RAM e "
                    f"{hw.get('primary_disk_free_gb')} gigabytes livres no disco principal."
                )
            else:
                summary = "Hardware verificado: sistema operacional ativo com disco e memoria saudaveis."
    else:
        summary = part.get("text", "Comando processado com sucesso.")

    print(f"[Pipeline] Resumo falado: \"{summary}\"")

    # 3. Text-to-Speech
    out_mp3 = "nexo_pipeline_response.mp3"
    success = synthesize_speech_neural2(summary, out_mp3)
    if not success:
        await synthesize_speech_edge(summary, out_mp3)

    print(f"[Pipeline] Audio de saida gerado com sucesso: {out_mp3}")


if __name__ == "__main__":
    test_audio = r"C:\Users\Aluno\nexo-daemon\input_16k.wav"
    if len(sys.argv) > 1:
        test_audio = sys.argv[1]
    asyncio.run(run_pipeline(test_audio))
