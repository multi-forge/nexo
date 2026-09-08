#!/usr/bin/env python3
"""
Testes do IA Router: a IA decide tudo. Sem rede real (mocks).
Cobre: credencial env->GCP CLI->erro, system prompt, tools,
mapeamento function->rota e route_via_ia com HTTP mockado.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))

import ia_router as ia
from ia_router import (
    SYSTEM_PROMPT,
    TOOLS_DECLARATION,
    map_function_call_to_route,
    resolve_gemini_credential,
    route_via_ia,
    MissingCredentialError,
    IaRouterError,
)


def _fn_response(name, args=None):
    return {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": name, "args": args or {}}}]}}]}


def _text_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class _FakeResp:
    def __init__(self, data):
        self._data = json.dumps(data).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --- credencial: env -> gcp cli -> erro --------------------------------------

def test_credential_prefers_env_over_gcloud(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  key-env-123  ")
    with patch.object(ia, "_gcloud_access_token") as g:
        kind, cred = resolve_gemini_credential()
        assert (kind, cred) == ("apikey", "key-env-123")
        g.assert_not_called()


def test_credential_falls_back_to_gcloud_cli(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch.object(ia, "_gcloud_access_token", return_value="ya29.token-gcp"):
        kind, cred = resolve_gemini_credential()
        assert (kind, cred) == ("vertex_token", "ya29.token-gcp")


def test_credential_missing_raises_no_local_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch.object(ia, "_gcloud_access_token", return_value=None):
        try:
            resolve_gemini_credential()
        except MissingCredentialError as e:
            assert "gcloud" in str(e).lower()
        else:
            raise AssertionError("deveria levantar MissingCredentialError")


def test_gcloud_helper_uses_gcloud_cmd_on_windows():
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(stdout="ya29.x\n", stderr="")
        # força ramo nt via os.name patch
        with patch.object(ia.os, "name", "nt"):
            token = ia._gcloud_access_token()
        assert token == "ya29.x"
        assert run.call_args[0][0][0] in ("gcloud.cmd", "gcloud")


# --- system prompt + tools: a IA tem tudo para decidir ------------------------

def test_system_prompt_covers_all_routes_and_no_echo():
    low = SYSTEM_PROMPT.lower()
    for tool in ["get_system_datetime", "check_system_hardware",
                 "list_project_files", "start_agy_task", "computer_use_action"]:
        assert tool in SYSTEM_PROMPT
    assert "nunca invente" in low
    assert "sem" in low and "eco" in low or "sem ecoar" in low


def test_tools_declaration_has_five_functions():
    names = {fn["name"] for t in TOOLS_DECLARATION for fn in t["functionDeclarations"]}
    assert names == {"get_system_datetime", "check_system_hardware",
                     "list_project_files", "start_agy_task", "computer_use_action"}


# --- mapeamento function -> rota ----------------------------------------------

def test_map_datetime_to_fast_tool():
    assert map_function_call_to_route("get_system_datetime") == (
        "ROUTE_2_FAST_TOOL", {"tool": "get_system_datetime"})


def test_map_hardware_and_project_to_fast_tool():
    assert map_function_call_to_route("check_system_hardware")[0] == "ROUTE_2_FAST_TOOL"
    assert map_function_call_to_route("list_project_files")[0] == "ROUTE_2_FAST_TOOL"


def test_map_agy_task_keeps_prompt():
    route, payload = map_function_call_to_route(
        "start_agy_task", {"prompt": "refatore o auth", "description": "refatorar auth"})
    assert route == "ROUTE_3_AGY_TASK"
    assert payload["task"] == "refatore o auth"


def test_map_computer_use_keeps_action():
    route, payload = map_function_call_to_route(
        "computer_use_action",
        {"action": "screenshot", "target": "tela", "description": "capturar tela"})
    assert route == "ROUTE_4_COMPUTER_USE"
    assert payload["action"] == "screenshot"


def test_map_unknown_function_raises():
    try:
        map_function_call_to_route("funcao_inventada")
    except IaRouterError:
        pass
    else:
        raise AssertionError("function desconhecida deveria levantar IaRouterError")


# --- route_via_ia com HTTP mockado --------------------------------------------

def _patch_credential(monkeypatch, kind="apikey", cred="k"):
    monkeypatch.setenv("GEMINI_API_KEY", cred if kind == "apikey" else "")
    if kind == "vertex_token":
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        return patch.object(ia, "_gcloud_access_token", return_value=cred)
    return patch.object(ia, "_gcloud_access_token", return_value=None)


def test_route_datetime_via_ia(monkeypatch):
    with _patch_credential(monkeypatch):
        with patch.object(ia.urllib.request, "urlopen",
                          return_value=_FakeResp(_fn_response("get_system_datetime"))):
            assert route_via_ia("qual e a data e hora atual do sistema") == (
                "ROUTE_2_FAST_TOOL", {"tool": "get_system_datetime"})


def test_route_vague_input_returns_ia_text_not_echo(monkeypatch):
    with _patch_credential(monkeypatch):
        with patch.object(ia.urllib.request, "urlopen",
                          return_value=_FakeResp(_text_response(
                              "Tranquilo, vamos por partes. Quer a hora ou o sistema?"))):
            route, payload = route_via_ia("nem sei kk")
            assert route == "ROUTE_1_DIALOGUE"
            assert "nem sei kk" not in payload["direct_response"]


def test_route_uses_vertex_bearer_when_no_apikey(monkeypatch):
    ctx = _patch_credential(monkeypatch, kind="vertex_token", cred="ya29.vert")
    captured = {}

    def fake_urlopen(req, timeout=15):
        captured["auth"] = req.get_header("Authorization")
        captured["url"] = req.full_url
        return _FakeResp(_text_response("Ola! Como posso ajudar?"))

    with ctx, patch.object(ia.urllib.request, "urlopen", side_effect=fake_urlopen):
        route, _ = route_via_ia("oi")
        assert route == "ROUTE_1_DIALOGUE"
        assert captured["auth"] == "Bearer ya29.vert"
        assert "aiplatform.googleapis.com" in captured["url"]


def test_route_propagates_error_without_local_fallback(monkeypatch):
    with _patch_credential(monkeypatch):
        with patch.object(ia.urllib.request, "urlopen",
                          side_effect=Exception("rede fora")):
            try:
                route_via_ia("oi")
            except IaRouterError:
                pass
            else:
                raise AssertionError("falha de rede deveria propagar, sem fallback local")


def test_route_empty_candidates_raises(monkeypatch):
    with _patch_credential(monkeypatch):
        with patch.object(ia.urllib.request, "urlopen",
                          return_value=_FakeResp({"candidates": []})):
            try:
                route_via_ia("oi")
            except IaRouterError:
                pass
            else:
                raise AssertionError("candidates vazio deveria levantar IaRouterError")
