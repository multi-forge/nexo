#!/usr/bin/env python3
"""
Testes das pontes no modelo 100% IA (sem rede real).
live_bridge usa system prompt/tools canônicos; pipeline usa credencial
env->GCP CLI; cliente mobile delega à IA (mockada aqui).
"""
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "orchestrator"))
sys.path.insert(0, str(REPO / "client-mobile"))

import live_bridge as lb
import pipeline_stt_tts as pl
import ia_router as ia


def test_live_bridge_uses_canonical_prompt_and_tools():
    import inspect
    src = inspect.getsource(lb.run_live_session)
    assert "IA_TOOLS" in src or "get_system_datetime" in src
    assert "IA_SYSTEM_PROMPT" in src or "Nunca invente" in inspect.getsource(lb)


def test_live_bridge_requires_apikey_for_websocket():
    with patch.object(lb, "resolve_gemini_credential",
                      return_value=("vertex_token", "ya29.x")):
        try:
            lb._live_api_key()
        except Exception as e:
            assert "API key" in str(e) or "Live" in str(e)
        else:
            raise AssertionError("token Vertex nao autentica o Live: deveria levantar")


def test_live_bridge_datetime_helper_returns_spoken():
    res = lb.query_system_datetime()
    assert "spoken" in res
    assert any(c.isdigit() for c in res["spoken"])


def test_live_bridge_project_helper_returns_files():
    res = lb.query_project_status()
    assert res["count"] > 0
    assert len(res["files"]) > 0


def test_pipeline_uses_canonical_prompt_and_credential_chain():
    import inspect
    src = inspect.getsource(pl.dispatch_llm_orchestrator)
    assert "SYSTEM_PROMPT" in src
    assert "resolve_gemini_credential" in src
    assert "Sem fallback local" in src or "sem fallback" in src.lower()


def test_client_wrapper_delegates_to_ia_not_keywords():
    import simulate_android_client as mob
    with patch.object(mob, "route_via_ia",
                      return_value=("ROUTE_2_FAST_TOOL", {"tool": "get_system_datetime"})):
        route, payload = mob.classify_cognitive_route("qual e a data e hora atual do sistema")
        assert route == "ROUTE_2_FAST_TOOL"
        assert payload == "get_system_datetime"
    with patch.object(mob, "route_via_ia",
                      return_value=("ROUTE_1_DIALOGUE",
                                    {"direct_response": "Tranquilo, vamos por partes?"})):
        route, payload = mob.classify_cognitive_route("nem sei kk")
        assert route == "ROUTE_1_DIALOGUE"
        assert "nem sei kk" not in payload


def test_client_propagates_ia_error_without_local_fallback():
    import simulate_android_client as mob
    with patch.object(mob, "route_via_ia", side_effect=ia.IaRouterError("rede fora")):
        try:
            mob.classify_cognitive_route("oi")
        except ia.IaRouterError:
            pass
        else:
            raise AssertionError("erro da IA deveria propagar, sem resposta local")
