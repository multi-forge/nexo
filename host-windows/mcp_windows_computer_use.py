#!/usr/bin/env python3
"""
JARVIS-DEV: MCP Server para Real Computer Use no Windows
=========================================================
Implementa o protocolo Model Context Protocol (MCP) via JSON-RPC / stdio.
Permite ao Antigravity e ao Gemini Live controlarem o desktop Windows com:
- Clique e movimento de mouse nativo (Win32 API / SendInput)
- Digitação e atalhos de teclado (Win32 API)
- Gerenciamento e foco de janelas
- Captura de tela em tempo real
"""

import sys
import io

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import base64
import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

# Win32 API para controle nativo sem dependências pesadas
if sys.platform == "win32":
    import ctypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
else:
    user32 = None
    gdi32 = None

SCREENSHOT_PATH = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Aluno"), "jarvis_computer_use_screen.png")

TOOLS = [
    {
        "name": "desktop_screenshot",
        "description": "Captura um screenshot completo da tela do Windows para inspeção visual de elementos e janelas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Motivo da captura"}
            }
        }
    },
    {
        "name": "mouse_click",
        "description": "Move o cursor do mouse e clica em uma coordenada (X, Y) da tela.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Coordenada X em pixels"},
                "y": {"type": "integer", "description": "Coordenada Y em pixels"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left", "description": "Botão do mouse"},
                "double_click": {"type": "boolean", "default": False, "description": "Se True, executa duplo clique"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "mouse_move",
        "description": "Move o cursor do mouse para a coordenada (X, Y) sem clicar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Coordenada X"},
                "y": {"type": "integer", "description": "Coordenada Y"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "keyboard_type",
        "description": "Digita um texto na janela que está atualmente focada no Windows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto a ser digitado"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "keyboard_hotkey",
        "description": "Pressiona uma combinação de teclas de atalho (ex: ['ctrl', 'c'], ['alt', 'tab'], ['win', 'r']).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de teclas (ex: ['ctrl', 'shift', 'esc'], ['alt', 'f4'], ['enter'])"
                }
            },
            "required": ["keys"]
        }
    },
    {
        "name": "window_list",
        "description": "Lista todas as janelas abertas no Windows com seus títulos e identificadores.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "window_focus",
        "description": "Traz uma janela específica para o primeiro plano pelo nome ou parte do título.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title_query": {"type": "string", "description": "Texto contido no título da janela (ex: 'Visual Studio Code', 'Chrome', 'Notepad')"}
            },
            "required": ["title_query"]
        }
    },
    {
        "name": "browser_open_url",
        "description": "Abre uma URL no navegador padrão do Windows ou ativa uma nova aba.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL a abrir (ex: 'https://github.com')"}
            },
            "required": ["url"]
        }
    }
]

# ─── Implementação das Ações Win32 / PowerShell ───────────────────────

def do_screenshot() -> str:
    ps = f"""
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $bmp = New-Object Drawing.Bitmap([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width, [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height)
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen(0,0,0,0, $bmp.Size)
    $bmp.Save('{SCREENSHOT_PATH}')
    """
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    if os.path.exists(SCREENSHOT_PATH):
        with open(SCREENSHOT_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"SCREENSHOT_SAVED: {SCREENSHOT_PATH} (base64 length: {len(b64)})"
    return "Erro ao capturar tela"

def do_mouse_click(x: int, y: int, button: str = "left", double_click: bool = False):
    if user32:
        user32.SetCursorPos(x, y)
        time.sleep(0.05)
        # MOUSEEVENTF_LEFTDOWN = 0x0002, MOUSEEVENTF_LEFTUP = 0x0004
        # MOUSEEVENTF_RIGHTDOWN = 0x0008, MOUSEEVENTF_RIGHTUP = 0x0010
        down = 0x0008 if button == "right" else 0x0002
        up = 0x0010 if button == "right" else 0x0004
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.02)
        user32.mouse_event(up, 0, 0, 0, 0)
        if double_click:
            time.sleep(0.08)
            user32.mouse_event(down, 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(up, 0, 0, 0, 0)
    else:
        ps = f"[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x}, {y})"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    return f"Mouse clicou em ({x}, {y}) [botão: {button}, duplo: {double_click}]"

def do_mouse_move(x: int, y: int):
    if user32:
        user32.SetCursorPos(x, y)
    else:
        ps = f"[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x}, {y})"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    return f"Mouse movido para ({x}, {y})"

def do_keyboard_type(text: str):
    # Envia texto através de SendKeys no PowerShell
    safe_text = text.replace("'", "''").replace("{", "{{}").replace("}", "{}}").replace("+", "{+}").replace("^", "{^}").replace("%", "{%}")
    ps = f"""
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.SendKeys]::SendWait('{safe_text}')
    """
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    return f"Texto digitado: {text[:50]}"

def do_keyboard_hotkey(keys: List[str]):
    # Mapeamento de teclas para SendKeys
    key_map = {
        "ctrl": "^", "control": "^",
        "alt": "%",
        "shift": "+",
        "win": "^{ESC}",
        "enter": "{ENTER}", "return": "{ENTER}",
        "esc": "{ESC}", "escape": "{ESC}",
        "tab": "{TAB}",
        "backspace": "{BACKSPACE}",
        "delete": "{DELETE}",
        "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
        "space": " "
    }
    modifiers = ""
    main_key = ""
    for k in keys:
        lk = k.lower()
        if lk in ["ctrl", "control", "alt", "shift"]:
            modifiers += key_map[lk]
        elif lk in key_map:
            main_key = key_map[lk]
        else:
            main_key = lk

    send_seq = f"{modifiers}{main_key}"
    ps = f"""
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.SendKeys]::SendWait('{send_seq}')
    """
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    return f"Atalho executado: {' + '.join(keys)}"

def do_window_list():
    ps = "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json"
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return res.stdout.strip()

def do_window_focus(title_query: str):
    ps = f"""
    $p = Get-Process | Where-Object {{$_.MainWindowTitle -like '*{title_query}*'}} | Select-Object -First 1
    if ($p) {{
        $wshell = New-Object -ComObject WScript.Shell
        $wshell.AppActivate($p.Id)
        Write-Output "Janela '$($p.MainWindowTitle)' (PID: $($p.Id)) focada com sucesso."
    }} else {{
        Write-Output "Nenhuma janela encontrada com termo: '{title_query}'"
    }}
    """
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return res.stdout.strip()

def do_browser_open(url: str):
    subprocess.Popen(["cmd", "/c", "start", url], shell=True)
    return f"Navegador aberto em: {url}"

# ─── Loop de Mensagens JSON-RPC (MCP Stdio) ───────────────────────────

def handle_rpc_call(request: Dict[str, Any]) -> Dict[str, Any]:
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "windows-computer-use", "version": "1.0.0"}
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS}
        }

    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})

        try:
            if name == "desktop_screenshot":
                content = do_screenshot()
            elif name == "mouse_click":
                content = do_mouse_click(args["x"], args["y"], args.get("button", "left"), args.get("double_click", False))
            elif name == "mouse_move":
                content = do_mouse_move(args["x"], args["y"])
            elif name == "keyboard_type":
                content = do_keyboard_type(args["text"])
            elif name == "keyboard_hotkey":
                content = do_keyboard_hotkey(args["keys"])
            elif name == "window_list":
                content = do_window_list()
            elif name == "window_focus":
                content = do_window_focus(args["title_query"])
            elif name == "browser_open_url":
                content = do_browser_open(args["url"])
            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Tool '{name}' não encontrada."}}

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(content)}]
                }
            }
        except Exception as err:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Erro na execução da tool: {str(err)}"}],
                    "isError": True
                }
            }

    elif method == "notifications/initialized":
        return None

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Método '{method}' não suportado."}
    }

def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line.strip())
            resp = handle_rpc_call(req)
            if resp:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"MCP Stdio Error: {e}\n")
            sys.stderr.flush()

if __name__ == "__main__":
    main()
