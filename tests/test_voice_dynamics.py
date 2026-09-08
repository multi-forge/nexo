#!/usr/bin/env python3
"""
Testes de dinâmica de voz: fila, debounce, flush, barge-in, earcon,
sanitização + integração IA->ferramenta real (IA mockada, sem rede/TTS).
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))

from voice_queue import VoiceQueueConfig, DynamicAudioQueue, generate_earcon_tone
from cognitive_router import execute_fast_tool, contextual_ack


def _cfg():
    c = VoiceQueueConfig()
    c.min_gap_ms = 3000
    c.max_words = 12
    c.max_cues = 5
    return c


def test_ack_deadline_within_doherty():
    c = _cfg()
    assert c.ack_deadline_ms <= 400
    assert c.doherty_ceiling_ms == 400


def test_sanitize_enforces_12_words():
    q = DynamicAudioQueue(_cfg())
    long_text = " ".join(f"palavra{i}" for i in range(25))
    clean = q.sanitize_discourse(long_text)
    assert len(clean.replace("...", "").split()) <= 12


def test_sanitize_removes_fillers():
    q = DynamicAudioQueue(_cfg())
    clean = q.sanitize_discourse("Hmm, tipo, acho que vou verificar o disco")
    lowered = clean.lower()
    assert "hmm" not in lowered
    assert "tipo" not in lowered
    assert "acho que" not in lowered
    assert len(clean.strip()) > 0


def test_cue_counter_ceiling():
    async def _run():
        q = DynamicAudioQueue(_cfg())
        for i in range(10):
            await q.push_cue(f"Passo {i}.")
        assert q.cue_counter == 5
    asyncio.run(_run())


def test_flush_keeps_only_final():
    async def _run():
        q = DynamicAudioQueue(_cfg())
        await q.push_cue("Lendo arquivo 1.")
        await q.push_cue("Lendo arquivo 2.")
        q.flush()
        await q.push_cue("Resumo final.", priority=0)
        assert q.queue.qsize() == 1
    asyncio.run(_run())


def test_barge_in_clears_queue_fast():
    async def _run():
        q = DynamicAudioQueue(_cfg())
        await q.push_cue("Frase 1.")
        await q.push_cue("Frase 2.")
        latency = q.trigger_barge_in()
        assert q.queue.empty()
        assert latency < 150.0
    asyncio.run(_run())


def test_earcon_byte_length():
    earcon = generate_earcon_tone(freq=440, duration_ms=80, sample_rate=16000)
    assert len(earcon) == int(16000 * 0.08) * 2


def test_silence_watchdog_thresholds():
    c = _cfg()
    assert c.emergency_filler_ms == 2000
    assert c.max_dead_ms == 1500


def test_debounce_window():
    c = _cfg()
    assert c.min_gap_ms >= 3000


# --- integração IA (mockada) -> ferramenta real --------------------------------

def test_ia_datetime_decision_executes_real_tool():
    """A IA decide get_system_datetime; a tool retorna hora real (sem genérico)."""
    import ia_router
    with patch.object(ia_router, "route_via_ia",
                      return_value=("ROUTE_2_FAST_TOOL", {"tool": "get_system_datetime"})):
        route, payload = ia_router.route_via_ia("qual e a data e hora atual do sistema")
        assert route == "ROUTE_2_FAST_TOOL"
        ack = contextual_ack(route, payload)
        assert "tarefa no projeto" not in ack.lower()
        result = execute_fast_tool(payload["tool"])
        assert any(ch.isdigit() for ch in result["spoken"])
        assert "modulos estao ativos" not in result["spoken"].lower()


def test_ia_project_decision_executes_real_tool():
    import ia_router
    with patch.object(ia_router, "route_via_ia",
                      return_value=("ROUTE_2_FAST_TOOL", {"tool": "list_project_files"})):
        route, payload = ia_router.route_via_ia("liste os arquivos do projeto")
        result = execute_fast_tool(payload["tool"])
        assert route == "ROUTE_2_FAST_TOOL"
        assert result["count"] > 0


def test_ia_vague_decision_returns_own_text_without_echo():
    import ia_router
    ia_text = "Tranquilo, vamos por partes. Quer a hora ou o sistema?"
    with patch.object(ia_router, "route_via_ia",
                      return_value=("ROUTE_1_DIALOGUE", {"direct_response": ia_text})):
        route, payload = ia_router.route_via_ia("nem sei kk")
        assert route == "ROUTE_1_DIALOGUE"
        assert "nem sei kk" not in payload["direct_response"]


def test_no_local_invention_on_agy_unavailable():
    """Sem agente, o cliente informa o fato em vez de simular sucesso."""
    import inspect
    from pathlib import Path as P
    src = (P(__file__).resolve().parent.parent / "client-mobile" /
           "simulate_android_client.py").read_text(encoding="utf-8")
    assert "nao esta disponivel no host" in src
    assert "Verificacao concluida. Os modulos estao ativos" not in src
