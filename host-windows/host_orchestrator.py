#!/usr/bin/env python3
"""
Nexo: Host Orchestrator com Computer Use Total (Versao de Producao)
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import base64
import json
import os
import subprocess
import time
import uuid

import websockets
from playwright.async_api import async_playwright

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
HOST_PORT = 8765
PROJECT_DIR = r"C:\Users\Aluno"
AGY_PATH = r"C:\Users\Aluno\AppData\Local\agy\bin\agy.exe"
MODEL = "gemini-3.1-flash-live-preview"

GEMINI_WS_URL = (
    f"wss://generativelanguage.googleapis.com/ws/"
    f"google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    f"?key={GEMINI_API_KEY}"
)

SYSTEM_PROMPT = """
Voce e o Nexo, copiloto de desenvolvimento e automacao no Windows com visao e Computer Use.
DIRETRIZES DE RESPOSTA:
1. Fale SEMPRE em portugues do Brasil de forma concisa, direta e natural (maximo 6 a 12 palavras por turno).
2. Ao receber o conteudo de uma pagina web, diga a manchete principal e o resultado de forma leve e rapida.
3. NUNCA use tom teatral de mordomo de cinema ou palavras rebuscadas.
"""

TOOLS_CONFIG = [
    {
        "functionDeclarations": [
            {
                "name": "browser_navigate",
                "description": "Navega para uma URL no navegador web, extrai o conteudo e tira um screenshot.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {"type": "STRING", "description": "URL a acessar"}
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "powershell_cmd",
                "description": "Executa comandos no terminal PowerShell do Windows.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {"type": "STRING", "description": "Comando PowerShell"}
                    },
                    "required": ["command"]
                }
            }
        ]
    }
]

class BrowserManager:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None

    async def init_browser(self):
        if not self.browser:
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(headless=True)
            self.page = await self.browser.new_page()
            await self.page.set_viewport_size({"width": 1280, "height": 800})
            print("[ComputerUse] Chromium ativo na RAM!")

    async def navigate_and_read(self, url: str) -> dict:
        await self.init_browser()
        print(f"[ComputerUse] Navegando para: {url}")
        if not url.startswith("http"):
            url = "https://" + url
        await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = await self.page.title()
        
        # Tenta pegar manchete específica ou texto geral
        try:
            top_heading = await self.page.inner_text(".titleline > a, h1, h2")
        except Exception:
            top_heading = title

        # Salva screenshot
        shot_path = os.path.join(PROJECT_DIR, "nexo_latest_screenshot.png")
        await self.page.screenshot(path=shot_path)
        print(f"[ComputerUse] Screenshot salvo em: {shot_path}")
        
        return {
            "title": title,
            "top_headline": top_heading,
            "status": "SUCCESS"
        }

browser_mgr = BrowserManager()

async def handle_client(client_ws):
    print(f"\n[Gateway] Celular conectado: {client_ws.remote_address}")
    try:
        async with websockets.connect(GEMINI_WS_URL, max_size=10*2**20) as gemini_ws:
            setup_msg = {
                "setup": {
                    "model": f"models/{MODEL}",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Aoede"}}
                        }
                    },
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "tools": TOOLS_CONFIG
                }
            }
            await gemini_ws.send(json.dumps(setup_msg))
            await gemini_ws.recv()
            print("[Gateway] Gemini Live 3.1 Pronto com Computer Use!")

            async def client_to_gemini():
                try:
                    async for msg in client_ws:
                        if isinstance(msg, bytes) and len(msg) > 1 and msg[0] == 0x01:
                            b64 = base64.b64encode(msg[1:]).decode("utf-8")
                            await gemini_ws.send(json.dumps({"realtimeInput": {"mediaChunks": [{"mimeType": "audio/pcm;rate=16000", "data": b64}]}}))
                        elif isinstance(msg, bytes) and len(msg) > 0 and msg[0] == 0x03:
                            await gemini_ws.send(json.dumps({"clientContent": {"turnComplete": True}}))
                        elif isinstance(msg, str):
                            try:
                                d = json.loads(msg)
                                p = d["clientContent"]["turns"][0]["parts"][0]["text"] if "clientContent" in d else msg
                            except Exception:
                                p = msg
                            print(f"[Prompt Recebido]: {p}")
                            await gemini_ws.send(json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{"text": p}]}], "turnComplete": True}}))
                except Exception as e:
                    print(f"Erro client_to_gemini: {e}")

            async def gemini_to_client():
                try:
                    async for raw in gemini_ws:
                        data = json.loads(raw)
                        sc = data.get("serverContent", {})
                        mt = sc.get("modelTurn", {})

                        # Envia audio sintetizado para o celular (Frame 0x02)
                        for part in mt.get("parts", []):
                            if "inlineData" in part:
                                b64 = part["inlineData"].get("data", "")
                                if b64:
                                    await client_ws.send(bytes([0x02]) + base64.b64decode(b64))

                        # Processa Tool Calls
                        tc = data.get("toolCall", {})
                        if tc.get("functionCalls"):
                            for fc in tc["functionCalls"]:
                                fname = fc.get("name")
                                fargs = fc.get("args", {})
                                fid = fc.get("id")
                                print(f"🔧 [Tool Call]: {fname}({fargs})")

                                if fname == "browser_navigate":
                                    result = await browser_mgr.navigate_and_read(fargs["url"])
                                elif fname == "powershell_cmd":
                                    cmd = fargs.get("command", "hostname")
                                    proc = await asyncio.create_subprocess_exec(
                                        "powershell", "-Command", cmd,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                        cwd=PROJECT_DIR
                                    )
                                    stdout, stderr = await proc.communicate()
                                    out = (stdout.decode("latin-1") + stderr.decode("latin-1")).strip()
                                    result = {"output": out[:1000]}
                                else:
                                    result = {"error": "Tool desconhecida"}

                                print(f"   ↳ Retorno da Tool enviado ao Gemini: {result}")
                                await gemini_ws.send(json.dumps({"toolResponse": {"functionResponses": [{"id": fid, "response": result}]}}))

                except Exception as e:
                    print(f"Erro gemini_to_client: {e}")

            await asyncio.gather(client_to_gemini(), gemini_to_client())

    except Exception as e:
        print(f"❌ Erro no handle_client: {e}")

async def main():
    print("================================================================")
    print(f"   NEXO WINDOWS HOST COM COMPUTER USE ATIVO (PORTA {HOST_PORT})")
    print("================================================================\n")
    await browser_mgr.init_browser()
    async with websockets.serve(handle_client, "0.0.0.0", HOST_PORT):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
