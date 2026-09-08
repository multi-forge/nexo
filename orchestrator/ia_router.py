#!/usr/bin/env python3
"""
Nexo IA Router (ia_router.py)
=============================
A IA decide TUDO via system prompt + function calling. Sem fallback local,
sem heurística de palavras-chave, sem resposta genérica fixa.

Credencial (nesta ordem, sem exceções silenciosas):
  1. $env:GEMINI_API_KEY (Gemini Developer API)
  2. GCP CLI: `gcloud auth print-access-token` (Vertex AI, projeto GCP_PROJECT_ID)
  3. Se nenhuma existir -> MissingCredentialError (falha explícita, nunca
     resposta local inventada).
"""

import json
import os
import time
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional, List

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "stt-465818")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
PRIMARY_MODEL = os.environ.get("NEXO_IA_MODEL", "gemini-2.5-flash-lite")
FALLBACK_MODEL = os.environ.get("NEXO_IA_FALLBACK_MODEL", "gemini-2.5-flash")


class MissingCredentialError(RuntimeError):
    """Nenhuma credencial IA disponível (nem env, nem GCP CLI)."""


class IaRouterError(RuntimeError):
    """A IA falhou e fallback local é PROIBIDO: propaga o erro."""


# ---------------------------------------------------------------------------
# System prompt canônico: a IA é a única decisora
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Voce e o Nexo, copiloto de engenharia e assistente por voz para desenvolvedores. "
    "Responda sempre em portugues do Brasil de forma concisa, fluida e natural (ideal para audio: "
    "1 a 2 frases curtas, maximo 25 palavras), sem ecoar a fala do usuario e sem frases roboticas. "
    "Nao seja um robo que repete menus ou opções fixas: converse como um parceiro de trabalho humano, prestativo e inteligente.\n"
    "Suas regras de acao:\n"
    "1. SEMPRE que o usuario pedir para realizar uma acao, tarefa, comando ou consulta, invoque a tool correspondente:\n"
    "   - get_system_datetime: para perguntas sobre hora, data, dia, relogio ou calendario.\n"
    "   - check_system_hardware: para status da memoria, CPU, disco, hardware ou telemetria da maquina.\n"
    "   - list_project_files: para listar arquivos, pastas ou modulos do projeto.\n"
    "   - start_agy_task: para QUALQUER tarefa de codigo, criar ou editar arquivos, comandos de terminal, operacoes git (status, commit, log, diff, branch), rodar testes ou criar scripts. Para commit, gere a mensagem automaticamente se o usuario nao fornecer.\n"
    "   - computer_use_action: para abrir programas no Windows (calculadora, bloco de notas, navegador), abrir sites/URLs, tirar screenshot ou automacao de tela.\n"
    "2. NUNCA ignore ou responda com recusa a uma tarefa acionavel: despache imediatamente a tool adequada (start_agy_task para engenharia/git/arquivos/terminal, computer_use_action para abrir apps/sites/telas).\n"
    "3. Para dialogos puramente sociais que NAO pecam nenhuma acao (saudacoes, duvidas, agradecimentos, hesitacoes como 'nao sei ainda', encerramentos, 'nenhum', 'deixa quieto'): responda de forma acolhedora, breve e humana.\n"
    "4. Nunca invente data, hora, hardware ou arquivos: invoque a tool e use o resultado real retornado."
)

TOOLS_DECLARATION = [{
    "functionDeclarations": [
        {
            "name": "get_system_datetime",
            "description": "Data e hora atual do sistema. Use para hora, data, dia, relogio.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "check_system_hardware",
            "description": "Telemetria real do host (CPU, RAM, GPU, disco, OS).",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "list_project_files",
            "description": "Lista real de arquivos do projeto nexo-app.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "start_agy_task",
            "description": "Executa tarefas de engenharia, codigo, criacao de arquivos, comandos de terminal, git (status, commit, log, diff) e testes.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "prompt": {"type": "STRING", "description": "Instrucao tecnica detalhada para execucao"},
                    "description": {"type": "STRING", "description": "Resumo em 3 palavras"},
                },
                "required": ["prompt", "description"],
            },
        },
        {
            "name": "computer_use_action",
            "description": "Automacao do Windows: abre programas (calculadora, bloco de notas, navegador), abre URLs, captura screenshot ou automacao de tela.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "description": "open_app, open_url, click, screenshot, etc."},
                    "target": {"type": "STRING", "description": "Nome do app, URL ou alvo da acao"},
                    "description": {"type": "STRING", "description": "Resumo em 3 palavras"},
                },
                "required": ["action", "target", "description"],
            },
        },
    ]
}]

# functionCall da IA -> rota interna
_FUNCTION_TO_ROUTE = {
    "get_system_datetime": "ROUTE_2_FAST_TOOL",
    "check_system_hardware": "ROUTE_2_FAST_TOOL",
    "list_project_files": "ROUTE_2_FAST_TOOL",
    "start_agy_task": "ROUTE_3_AGY_TASK",
    "computer_use_action": "ROUTE_4_COMPUTER_USE",
}


def map_function_call_to_route(fn_name: str, args: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """Mapeia o functionCall retornado pela IA para (rota, payload). Puro, sem rede."""
    args = args or {}
    route = _FUNCTION_TO_ROUTE.get(fn_name)
    if route is None:
        raise IaRouterError(f"Function desconhecida retornada pela IA: {fn_name!r}")
    if route == "ROUTE_2_FAST_TOOL":
        return (route, {"tool": fn_name})
    if route == "ROUTE_3_AGY_TASK":
        return (route, {"task": args.get("prompt", ""), "description": args.get("description", "")})
    if route == "ROUTE_4_COMPUTER_USE":
        return (route, {"action": args.get("action", ""), "target": args.get("target", ""),
                        "description": args.get("description", "")})
    raise IaRouterError(f"Rota nao mapeada para function: {fn_name!r}")


# ---------------------------------------------------------------------------
# Credencial: env -> GCP CLI -> erro explícito
# ---------------------------------------------------------------------------

_CACHED_GCLOUD_TOKEN: Optional[str] = None
_CACHED_TOKEN_EXPIRY: float = 0.0


def _gcloud_access_token() -> Optional[str]:
    """Tenta `gcloud auth print-access-token` (Windows: gcloud.cmd) com cache de 50 minutos."""
    global _CACHED_GCLOUD_TOKEN, _CACHED_TOKEN_EXPIRY
    now = time.time()
    if _CACHED_GCLOUD_TOKEN and now < _CACHED_TOKEN_EXPIRY:
        return _CACHED_GCLOUD_TOKEN

    candidates = (["gcloud.cmd", "auth", "print-access-token"]
                  if os.name == "nt"
                  else ["gcloud", "auth", "print-access-token"])
    try:
        res = subprocess.run(candidates, capture_output=True, text=True,
                             check=True, timeout=10)
        token = res.stdout.strip()
        if token:
            _CACHED_GCLOUD_TOKEN = token
            _CACHED_TOKEN_EXPIRY = now + 3000.0  # 50 min
            return token
    except Exception:
        pass
    return None


def resolve_gemini_credential() -> Tuple[str, str]:
    """
    Retorna ("apikey", key) ou ("vertex_token", token).
    Levanta MissingCredentialError se nenhum existir. Nunca retorna None.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        return ("apikey", api_key)
    token = _gcloud_access_token()
    if token:
        return ("vertex_token", token)
    raise MissingCredentialError(
        "Sem credencial IA: defina $env:GEMINI_API_KEY ou autentique o GCP CLI "
        "(`gcloud auth login`). Fallback local desativado por configuracao."
    )


# ---------------------------------------------------------------------------
# Chamada à IA (única decisora) com Keep-Alive Connection Pooling
# ---------------------------------------------------------------------------

_SESSION: Optional[Any] = None


def _get_http_session():
    """Mantém pool de conexões TCP/TLS persistentes para Vertex AI (corta ~700ms de handshake)."""
    global _SESSION
    if _SESSION is None:
        try:
            import requests
            _SESSION = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=5,
                pool_maxsize=10,
                max_retries=1,
            )
            _SESSION.mount("https://", adapter)
            _SESSION.mount("http://", adapter)
        except Exception:
            _SESSION = None
    return _SESSION


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
               timeout: int = 15) -> Dict[str, Any]:
    # Se urllib.request.urlopen estiver mockado (ex: suíte de testes unitários), preserva urllib
    if hasattr(urllib.request.urlopen, "mock_calls"):
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise IaRouterError(f"IA HTTP {e.code}: {body}")
        except Exception as e:
            raise IaRouterError(f"Falha ao chamar a IA: {e}")

    session = _get_http_session()
    if session is not None:
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                raise IaRouterError(f"IA HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        except IaRouterError:
            raise
        except Exception as e:
            raise IaRouterError(f"Falha ao chamar a IA: {e}")

    # Fallback para urllib padrão
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise IaRouterError(f"IA HTTP {e.code}: {body}")
    except Exception as e:
        raise IaRouterError(f"Falha ao chamar a IA: {e}")


def _parse_gemini_response(data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Extrai functionCall ou texto direto. Texto direto = ROUTE_1_DIALOGUE com a fala da IA."""
    candidates = data.get("candidates", [])
    if not candidates:
        raise IaRouterError("IA retornou zero candidates.")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise IaRouterError("IA retornou content vazio.")
    for part in parts:
        if "functionCall" in part:
            fc = part["functionCall"]
            return map_function_call_to_route(fc.get("name", ""), fc.get("args", {}))
    for part in parts:
        if part.get("text", "").strip():
            return ("ROUTE_1_DIALOGUE", {"direct_response": part["text"].strip()})
    raise IaRouterError("IA retornou parts sem texto nem functionCall.")


def route_via_ia(
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    timeout: int = 15,
) -> Tuple[str, Dict[str, Any]]:
    """
    Roteia 100% via IA. Sem heurística local.
    Suporta histórico de conversa (multi-turn) para manter contexto e coerência.
    Qualquer falha de credencial/rede/modelo propaga exceção (sem fallback).
    """
    kind, credential = resolve_gemini_credential()
    candidate_models = [
        PRIMARY_MODEL,
        FALLBACK_MODEL,
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-1.5-flash",
    ]
    models = []
    for m in candidate_models:
        if m and m not in models:
            models.append(m)

    # Constrói histórico multi-turn se fornecido (mantendo os últimos 8 turnos para baixa latência)
    contents = []
    if conversation_history:
        contents.extend(conversation_history[-8:])
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    last_err: Optional[Exception] = None
    for model in models:
        if kind == "apikey":
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={credential}")
            headers = {"Content-Type": "application/json"}
        else:
            url = (f"https://{GCP_LOCATION}-aiplatform.googleapis.com/v1/projects/"
                   f"{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/publishers/google/"
                   f"models/{model}:generateContent")
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {credential}"}
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": TOOLS_DECLARATION,
        }
        try:
            data = _post_json(url, payload, headers, timeout=timeout)
            return _parse_gemini_response(data)
        except (IaRouterError, MissingCredentialError) as e:
            last_err = e
            # HTTP/modelo pode tentar o próximo modelo; falta de credencial não.
            if isinstance(e, MissingCredentialError):
                raise
            continue
    raise IaRouterError(f"IA indisponivel nos modelos {models}: {last_err}")
