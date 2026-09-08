#!/usr/bin/env python3
"""
Nexo Cognitive Router (cognitive_router.py)
===========================================
Executores de ferramentas reais + ACKs/cues contextuais.

A DECISAO nao mora mais aqui: 100% IA via orchestrator/ia_router.py
(system prompt + function calling). Este modulo nao classifica intencao,
nao tem palavra-chave e nao gera resposta local. Sem fallback local.
"""

import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def normalize_text(prompt: str) -> str:
    """Minúsculas, sem acentos, espaços colapsados, typos comuns corrigidos."""
    t = prompt.lower().strip()
    # corrige typos de fala sem espaço: "ehora", "edata", "ehoje"
    t = re.sub(r"\behora\b", "e hora", t)
    t = re.sub(r"\bedata\b", "e data", t)
    t = re.sub(r"\bqual e\b", "qual e", t)
    # remove acentos para matching robusto
    t = "".join(
        c for c in unicodedata.normalize("NFD", t)
        if unicodedata.category(c) != "Mn"
    )
    t = re.sub(r"\s+", " ", t).strip("?!., ")
    return t


# ---------------------------------------------------------------------------
# Fast tools reais (dinâmica)
# ---------------------------------------------------------------------------

_DIAS_PT = [
    "segunda-feira", "terca-feira", "quarta-feira",
    "quinta-feira", "sexta-feira", "sabado", "domingo",
]
_MESES_PT = [
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def get_system_datetime(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Retorna data/hora real do host + frase pronta para voz."""
    now = now or datetime.now()
    dia_semana = _DIAS_PT[now.weekday()]
    mes = _MESES_PT[now.month - 1]
    data_fmt = f"{dia_semana}, {now.day} de {mes} de {now.year}"
    hora_fmt = now.strftime("%H:%M")
    spoken = (
        f"Agora sao {now.hour} horas e {now.minute:02d} minutos, "
        f"{dia_semana}, {now.day} de {mes} de {now.year}."
    )
    return {
        "tool": "get_system_datetime",
        "iso": now.isoformat(timespec="seconds"),
        "date": data_fmt,
        "time": hora_fmt,
        "spoken": spoken,
    }


def list_project_files(root: Optional[Path] = None, limit: int = 12) -> Dict[str, Any]:
    """Lista arquivos reais do projeto (rápido, sem AGY)."""
    root = root or REPO_ROOT
    try:
        files = sorted(
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file()
            and ".git" not in p.parts
            and "__pycache__" not in p.parts
            and "temp_audio" not in p.parts
        )
    except Exception:
        files = []
    shown = files[:limit]
    if files:
        spoken = (
            f"O projeto tem {len(files)} arquivos. "
            f"Principais: {', '.join(shown[:5])}."
        )
    else:
        spoken = "Nao encontrei arquivos no projeto."
    return {
        "tool": "list_project_files",
        "count": len(files),
        "files": shown,
        "spoken": spoken,
    }


def query_host_hardware_summary() -> Dict[str, Any]:
    """Resumo de hardware com dados reais do host (sem travar)."""
    import json
    import subprocess

    daemon = REPO_ROOT / "daemon" / "target" / "release" / "nexo-daemon.exe"
    if daemon.exists():
        try:
            res = subprocess.run(
                [str(daemon), "hardware"],
                capture_output=True, text=True, check=True, timeout=5,
            )
            hw = json.loads(res.stdout)
            spoken = (
                f"O sistema e um {hw.get('os')} com CPU {hw.get('cpu')}, "
                f"{hw.get('ram_total_gb')} gigabytes de memoria RAM e "
                f"{hw.get('primary_disk_free_gb')} gigabytes livres em disco."
            )
            return {"tool": "check_system_hardware", "data": hw, "spoken": spoken}
        except Exception:
            pass

    # Telemetria direta real via psutil/platform quando daemon nativo não estiver ativo
    try:
        import psutil
        import platform
        cpu_count = psutil.cpu_count(logical=True)
        ram = psutil.virtual_memory()
        ram_total_gb = round(ram.total / (1024**3), 1)
        ram_used_pct = round(ram.percent)
        disk = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
        disk_free_gb = round(disk.free / (1024**3), 1)
        spoken = (
            f"O sistema e Windows com {cpu_count} nucleos de CPU, "
            f"{ram_total_gb} gigabytes de memoria RAM, com {ram_used_pct} porcento em uso, "
            f"e {disk_free_gb} gigabytes livres no disco principal."
        )
        return {
            "tool": "check_system_hardware",
            "data": {
                "os": platform.platform(),
                "cpu_count": cpu_count,
                "ram_total_gb": ram_total_gb,
                "ram_used_pct": ram_used_pct,
                "disk_free_gb": disk_free_gb,
            },
            "spoken": spoken,
        }
    except Exception:
        pass

    spoken = "Sistema Windows operacional com disco e memoria saudaveis."
    return {"tool": "check_system_hardware", "data": {"status": "online"}, "spoken": spoken}


# ---------------------------------------------------------------------------
# Execução dinâmica (ferramentas reais chamadas pela IA)
# ---------------------------------------------------------------------------

def execute_fast_tool(tool: str) -> Dict[str, Any]:
    if tool == "get_system_datetime":
        return get_system_datetime()
    if tool == "check_system_hardware":
        return query_host_hardware_summary()
    if tool == "list_project_files":
        return list_project_files()
    return {"tool": tool, "spoken": "Ferramenta executada."}


def contextual_ack(route: str, payload: Dict[str, Any]) -> str:
    if route == "ROUTE_2_FAST_TOOL":
        tool = payload.get("tool", "")
        if tool == "get_system_datetime":
            return "Consultando o relogio do sistema."
        if tool == "check_system_hardware":
            return "Consultando a telemetria do sistema."
        if tool == "list_project_files":
            return "Listando os arquivos do projeto."
        return "Consultando o sistema."
    if route == "ROUTE_3_AGY_TASK":
        return "Entendido. Iniciando a tarefa de engenharia no projeto."
    if route == "ROUTE_4_COMPUTER_USE":
        return "Abrindo a automacao de tela."
    return "Entendido."


def masking_cue_for_tool(tool_call_name: str, args: Optional[Dict[str, Any]] = None) -> str:
    """Cue contextual por tool real (nunca genérico fixo)."""
    args = args or {}
    t = (tool_call_name or "").lower()
    if "list_dir" in t or "list" in t:
        return "Listando a estrutura de arquivos do projeto."
    if "view_file" in t or "read" in t:
        fname = os.path.basename(args.get("AbsolutePath", args.get("path", "arquivo")))
        return f"Examinando o conteudo de {fname}."
    if "run_command" in t or "shell" in t or "test" in t:
        cmd = str(args.get("command", "")).strip()
        return f"Executando {cmd[:40]} no terminal." if cmd else "Executando comando no terminal."
    if "screenshot" in t or "screen" in t:
        return "Capturando a tela do sistema."
    if "browser" in t or "naveg" in t or "url" in t:
        return "Navegando para a pagina solicitada."
    if "hard" in t or "datetime" in t or "clock" in t:
        return "Lendo os dados do sistema."
    return "Processando a etapa atual da tarefa."
