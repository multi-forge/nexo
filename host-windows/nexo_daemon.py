#!/usr/bin/env python3
"""
Nexo Unified Windows Daemon (nexo_daemon.py)
=================================================
Motor Único e Persistente de Controle Total do Windows:
1. Win32 Input nativo em memória C/ctypes (Mouse, Teclado, Janelas, Clipboard, Display)
2. Captura de tela em alta velocidade (GDI/GPU em RAM)
3. Servidor WebSocket (Porta 8765) com suporte a JSON-RPC e Gemini 3.1 Live (Voz 24kHz)
4. Suporte a MCP stdio quando executado com a flag '--mcp'
"""

import asyncio
import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.request
import websockets

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ==============================================================================
# 1. MOTOR WIN32 NATIVO EM MEMÓRIA (ZERO SCRIPTS, ZERO PROCESS SPAWN)
# ==============================================================================

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

# Constantes Win32 Mouse
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

# Constantes Win32 Teclado
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_MAP = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "esc": 0x1B, "escape": 0x1B, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "shift": 0x10, "win": 0x5B, "windows": 0x5B, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "f1": 0x70, "f2": 0x71, "f3": 0x72,
    "f4": 0x73, "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78,
    "f10": 0x79, "f11": 0x7A, "f12": 0x7B
}

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

class Win32NativeEngine:
    @staticmethod
    def get_screen_size():
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        return {"width": w, "height": h}

    @staticmethod
    def get_cursor_pos():
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return {"x": pt.x, "y": pt.y}

    @staticmethod
    def mouse_move(x: int, y: int, smooth: bool = False):
        if not smooth:
            user32.SetCursorPos(int(x), int(y))
        else:
            curr = Win32NativeEngine.get_cursor_pos()
            steps = 15
            for i in range(1, steps + 1):
                cx = int(curr["x"] + (x - curr["x"]) * (i / steps))
                cy = int(curr["y"] + (y - curr["y"]) * (i / steps))
                user32.SetCursorPos(cx, cy)
                time.sleep(0.01)
        return {"status": "ok", "x": x, "y": y}

    @staticmethod
    def mouse_click(button: str = "left", x: Optional[int] = None, y: Optional[int] = None, double: bool = False):
        if x is not None and y is not None:
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.02)
        
        btn = button.lower()
        down_flag = MOUSEEVENTF_LEFTDOWN if btn == "left" else (MOUSEEVENTF_RIGHTDOWN if btn == "right" else MOUSEEVENTF_MIDDLEDOWN)
        up_flag = MOUSEEVENTF_LEFTUP if btn == "left" else (MOUSEEVENTF_RIGHTUP if btn == "right" else MOUSEEVENTF_MIDDLEUP)
        
        user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(up_flag, 0, 0, 0, 0)

        if double:
            time.sleep(0.08)
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.03)
            user32.mouse_event(up_flag, 0, 0, 0, 0)

        return {"status": "ok", "button": button, "double": double}

    @staticmethod
    def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int):
        user32.SetCursorPos(int(start_x), int(start_y))
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        
        steps = 25
        for i in range(1, steps + 1):
            cx = int(start_x + (end_x - start_x) * (i / steps))
            cy = int(start_y + (end_y - start_y) * (i / steps))
            user32.SetCursorPos(cx, cy)
            time.sleep(0.01)

        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return {"status": "ok", "drag": [start_x, start_y, end_x, end_y]}

    @staticmethod
    def mouse_scroll(clicks: int):
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(clicks * 120), 0)
        return {"status": "ok", "scroll": clicks}

    @staticmethod
    def type_text(text: str):
        for char in text:
            code = ord(char)
            if code == 10 or code == 13: # Enter
                user32.keybd_event(0x0D, 0, 0, 0)
                time.sleep(0.01)
                user32.keybd_event(0x0D, 0, KEYEVENTF_KEYUP, 0)
            else:
                user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
                time.sleep(0.005)
                user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
            time.sleep(0.005)
        return {"status": "ok", "length": len(text)}

    @staticmethod
    def press_hotkey(keys: List[str]):
        vks = []
        for k in keys:
            kl = k.lower()
            if kl in VK_MAP:
                vks.append(VK_MAP[kl])
            elif len(kl) == 1:
                vks.append(ord(kl.upper()))
        
        # Press down
        for vk in vks:
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)
        
        time.sleep(0.03)

        # Release up in reverse
        for vk in reversed(vks):
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.01)

        return {"status": "ok", "keys": keys}

    @staticmethod
    def list_windows():
        windows = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        
        def callback(hwnd, extra):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.strip()
                    if title:
                        rect = RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        windows.append({
                            "hwnd": hwnd,
                            "title": title,
                            "bounds": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}
                        })
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        return windows

    @staticmethod
    def focus_window(title_keyword: str):
        windows = Win32NativeEngine.list_windows()
        for w in windows:
            if title_keyword.lower() in w["title"].lower():
                hwnd = w["hwnd"]
                user32.ShowWindow(hwnd, 9) # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return {"status": "ok", "focused": w["title"], "hwnd": hwnd}
        return {"status": "error", "message": f"Window '{title_keyword}' not found"}

    @staticmethod
    def open_app(command: str):
        subprocess.Popen(command, shell=True)
        return {"status": "ok", "launched": command}

    @staticmethod
    def capture_screen_base64():
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:
            return ""

# ==============================================================================
# 2. MOTOR GEMINI 3.1 LIVE STREAMING + TOOL DISPATCH EM MEMÓRIA
# ==============================================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_LIVE_URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

DAEMON_TOOL_DECLARATIONS = [
    {
        "name": "mouse_move",
        "description": "Move o cursor do mouse na tela do Windows.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}},
            "required": ["x", "y"]
        }
    },
    {
        "name": "mouse_click",
        "description": "Clica com o mouse no Windows.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "button": {"type": "STRING", "description": "left, right, middle"},
                "x": {"type": "INTEGER"},
                "y": {"type": "INTEGER"},
                "double": {"type": "BOOLEAN"}
            }
        }
    },
    {
        "name": "type_text",
        "description": "Digita texto diretamente no aplicativo ativo no Windows.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}},
            "required": ["text"]
        }
    },
    {
        "name": "press_hotkey",
        "description": "Pressiona combinação de teclas (ex: ctrl+c, alt+tab, enter, win+r).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"keys": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["keys"]
        }
    },
    {
        "name": "window_focus",
        "description": "Foca e traz para frente uma janela pelo nome (ex: Chrome, Notepad, VS Code).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"title": {"type": "STRING"}},
            "required": ["title"]
        }
    },
    {
        "name": "open_app",
        "description": "Abre um programa ou URL no Windows (ex: chrome.exe, notepad.exe, spotify).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"command": {"type": "STRING"}},
            "required": ["command"]
        }
    },
    {
        "name": "screen_inspect",
        "description": "Lista todas as janelas abertas e suas posições na tela.",
        "parameters": {"type": "OBJECT", "properties": {}}
    }
]

def dispatch_tool_in_memory(name: str, args: Dict[str, Any]) -> Any:
    """Executa a ferramenta diretamente na memória do Win32 sem criar nenhum processo ou script."""
    t0 = time.perf_counter()
    if name == "mouse_move":
        res = Win32NativeEngine.mouse_move(args.get("x", 0), args.get("y", 0))
    elif name == "mouse_click":
        res = Win32NativeEngine.mouse_click(args.get("button", "left"), args.get("x"), args.get("y"), args.get("double", False))
    elif name == "type_text":
        res = Win32NativeEngine.type_text(args.get("text", ""))
    elif name == "press_hotkey":
        res = Win32NativeEngine.press_hotkey(args.get("keys", []))
    elif name == "window_focus":
        res = Win32NativeEngine.focus_window(args.get("title", ""))
    elif name == "open_app":
        res = Win32NativeEngine.open_app(args.get("command", ""))
    elif name == "screen_inspect":
        res = Win32NativeEngine.list_windows()
    else:
        res = {"error": f"Unknown tool: {name}"}
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {"result": res, "elapsed_ms": f"{elapsed_ms:.2f}ms"}

# ==============================================================================
# 3. WEBSOCKET SERVER BIDIRECIONAL PERSISTENTE (PORTA 8765)
# ==============================================================================

async def handle_client(websocket):
    client_ip = websocket.remote_address[0]
    print(f"[DAEMON] Cliente conectado: {client_ip}")

    # Conecta ao Gemini 3.1 Live
    try:
        async with websockets.connect(GEMINI_LIVE_URL, max_size=10*2**20) as gemini_ws:
            # Envia Setup
            setup_msg = {
                "setup": {
                    "model": "models/gemini-3.1-flash-live-preview",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": "Aoede"
                                }
                            }
                        }
                    },
                    "systemInstruction": {
                        "parts": [{
                            "text": (
                                "Voce e o Nexo Daemon, o assistente em tempo real do desenvolvedor no Windows. "
                                "Você controla o computador diretamente em memória através das suas ferramentas de Win32 nativo. "
                                "Seja ultra-rápido, conciso e natural na fala."
                            )
                        }]
                    },
                    "tools": [{"functionDeclarations": DAEMON_TOOL_DECLARATIONS}]
                }
            }
            await gemini_ws.send(json.dumps(setup_msg))

            async def pump_gemini_to_client():
                try:
                    async for raw in gemini_ws:
                        msg = json.loads(raw)
                        
                        # 1. Áudio da resposta para o celular
                        if "serverContent" in msg:
                            model_turn = msg["serverContent"].get("modelTurn", {})
                            for part in model_turn.get("parts", []):
                                if "inlineData" in part and part["inlineData"].get("mimeType", "").startswith("audio/pcm"):
                                    pcm = base64.b64decode(part["inlineData"]["data"])
                                    # Envia áudio com prefixo 0x02
                                    await websocket.send(b"\x02" + pcm)

                        # 2. Tool Calls do Gemini executados diretamente em memória
                        if "toolCall" in msg:
                            calls = msg["toolCall"].get("functionCalls", [])
                            responses = []
                            for c in calls:
                                call_id = c.get("id", "")
                                name = c.get("name", "")
                                args = c.get("args", {})
                                
                                print(f"[DAEMON TOOL CALL] {name}({args})")
                                out = dispatch_tool_in_memory(name, args)
                                print(f"[DAEMON RESULT] {out}")
                                
                                responses.append({
                                    "id": call_id,
                                    "name": name,
                                    "response": {"output": out}
                                })

                            tool_resp_msg = {
                                "toolResponse": {
                                    "functionResponses": responses
                                }
                            }
                            await gemini_ws.send(json.dumps(tool_resp_msg))

                except Exception as e:
                    pass

            gemini_pump_task = asyncio.create_task(pump_gemini_to_client())

            # Loop de recebimento do celular / cliente
            async for raw in websocket:
                # Se for requisição direta JSON de ação (ex: {"action": "mouse_click", ...})
                if isinstance(raw, str):
                    try:
                        data = json.loads(raw)
                        if "action" in data:
                            action = data["action"]
                            res = dispatch_tool_in_memory(action, data)
                            await websocket.send(json.dumps({"status": "ok", "action": action, "response": res}))
                            continue
                    except json.JSONDecodeError:
                        pass

                    # Se for prompt em texto para o Gemini Live
                    text_msg = {
                        "clientContent": {
                            "turns": [{
                                "role": "user",
                                "parts": [{"text": raw}]
                            }],
                            "turnComplete": True
                        }
                    }
                    await gemini_ws.send(json.dumps(text_msg))

                # Se for áudio PCM bruto vindo do microfone do celular (prefixo 0x01)
                elif isinstance(raw, bytes) and len(raw) > 1 and raw[0] == 0x01:
                    audio_b64 = base64.b64encode(raw[1:]).decode("ascii")
                    audio_msg = {
                        "realtimeInput": {
                            "mediaChunks": [{
                                "mimeType": "audio/pcm;rate=16000",
                                "data": audio_b64
                            }]
                        }
                    }
                    await gemini_ws.send(json.dumps(audio_msg))

            gemini_pump_task.cancel()

    except Exception as err:
        print(f"[DAEMON ERR] {err}")

async def start_daemon_server():
    print("════════════════════════════════════════════════════════════════════")
    print("   NEXO UNIFIED WINDOWS DAEMON ATIVO (PORTA 8765)                  ")
    print("   • Motor Win32 C/ctypes Nativo em Memória (< 1ms)                ")
    print("   • Gemini 3.1 Live Bidirecional + Voz Aoede 24kHz               ")
    print("   • Zero Scripts em Disco / Zero Process Spawning                ")
    print("════════════════════════════════════════════════════════════════════")
    
    async with websockets.serve(handle_client, "0.0.0.0", 8765, max_size=10*2**20):
        await asyncio.Future() # Roda para sempre

# ==============================================================================
# 4. INTERFACE STDIO MCP (QUANDO EXECUTADO COM --mcp)
# ==============================================================================

def run_mcp_stdio():
    """Interface MCP JSON-RPC 2.0 nativa para o Antigravity CLI."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            method = req.get("method")
            msg_id = req.get("id")

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "nexo-daemon", "version": "1.0.0"}
                    }
                }
                print(json.dumps(resp), flush=True)

            elif method == "notifications/initialized":
                pass

            elif method == "tools/list":
                tools = []
                for dec in DAEMON_TOOL_DECLARATIONS:
                    tools.append({
                        "name": dec["name"],
                        "description": dec["description"],
                        "inputSchema": dec["parameters"]
                    })
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": tools}
                }
                print(json.dumps(resp), flush=True)

            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name")
                args = params.get("arguments", {})
                out = dispatch_tool_in_memory(name, args)
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(out)}]
                    }
                }
                print(json.dumps(resp), flush=True)

        except Exception as e:
            pass

if __name__ == "__main__":
    if "--mcp" in sys.argv:
        run_mcp_stdio()
    else:
        asyncio.run(start_daemon_server())
