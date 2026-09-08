#!/usr/bin/env python3
"""
Testes dos executores de ferramentas (cognitive_router sem decisor local).
A decisão é 100% IA (ia_router); aqui valida-se apenas que as tools
retornam dados reais e que ACKs/cues são contextuais. Sem rede.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))

from cognitive_router import (
    normalize_text,
    execute_fast_tool,
    contextual_ack,
    masking_cue_for_tool,
    get_system_datetime,
    list_project_files,
)


def test_normalize_removes_accents_and_collapses_spaces():
    assert normalize_text("  Qual   é a DATA? ") == "qual e a data"


def test_normalize_fixes_ehora_typo():
    assert "e hora" in normalize_text("qual e a data ehora atual do sistema")


def test_no_local_classifier_present():
    import cognitive_router as cr
    assert not hasattr(cr, "classify_cognitive_route"), \
        "decisor local removido: a IA (ia_router) decide"


def test_get_system_datetime_returns_real_data():
    fixed = datetime(2026, 9, 8, 15, 42, 0)
    res = get_system_datetime(fixed)
    assert res["time"] == "15:42"
    assert "2026-09-08T15:42:00" in res["iso"]
    assert "15 horas e 42 minutos" in res["spoken"]
    assert "setembro" in res["spoken"].lower()


def test_get_system_datetime_spoken_has_no_placeholder():
    res = execute_fast_tool("get_system_datetime")
    assert "modulos" not in res["spoken"].lower()
    assert any(c.isdigit() for c in res["spoken"])


def test_list_project_files_returns_real_files():
    res = list_project_files()
    assert res["count"] > 0
    assert len(res["files"]) > 0
    assert "modulos estao ativos" not in res["spoken"]
    assert str(res["count"]) in res["spoken"]


def test_execute_fast_tool_dispatch():
    for tool in ["get_system_datetime", "check_system_hardware", "list_project_files"]:
        res = execute_fast_tool(tool)
        assert res["spoken"] and len(res["spoken"]) > 5


def test_contextual_ack_datetime():
    ack = contextual_ack("ROUTE_2_FAST_TOOL", {"tool": "get_system_datetime"})
    assert "relogio" in ack.lower() or "sistema" in ack.lower()
    assert ack != "Entendido. Iniciando a tarefa no projeto."


def test_contextual_ack_differs_per_tool():
    a = contextual_ack("ROUTE_2_FAST_TOOL", {"tool": "get_system_datetime"})
    b = contextual_ack("ROUTE_2_FAST_TOOL", {"tool": "list_project_files"})
    assert a != b


def test_masking_cue_is_contextual():
    assert "arquivos" in masking_cue_for_tool("list_dir").lower()
    assert "Cargo.toml" in masking_cue_for_tool("view_file", {"AbsolutePath": "C:/x/Cargo.toml"})
    assert "tela" in masking_cue_for_tool("desktop_screenshot").lower()
    assert len(masking_cue_for_tool("unknown_xyz")) > 0
