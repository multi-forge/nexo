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
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional

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
    "Voce e o Nexo, copiloto de engenharia por voz. Responda sempre em "
    "portugues do Brasil, de forma concisa e natural (maximo 25 palavras), "
    "sem ecoar a fala do usuario e sem frases roboticas como 'Entendi: ...'. "
    "Voce decide a proxima acao EXCLUSIVAMENTE via function calling:\n"
    "- Perguntas de data, hora, dia, relogio ou calendario -> get_system_datetime.\n"
    "- Hardware, memoria, CPU, disco, temperatura ou status do sistema -> check_system_hardware.\n"
    "- Arquivos, modulos, estrutura do projeto, branch ou git status -> list_project_files.\n"
    "- Criar, refatorar, corrigir, implementar, testar ou commitar codigo -> start_agy_task.\n"
    "- Abrir navegador, clicar, digitar na tela, screenshot ou automacao visual -> computer_use_action.\n"
    "- Saudacao, identidade, agradecimento, ajuda, risada ('kk'), vagueza "
    "('nem sei', 'nao fez sentido', 'como assim') ou qualquer fala sem "
    "intencao acionavel -> responda DIRETO com texto curto e acolhedor que "
    "ofereca hora, sistema, projeto ou tela como opcoes. Nunca repita a fala "
    "crua do usuario.\n"
    "Nunca invente data, hora, hardware ou arquivos: invoque a tool e use o "
    "resultado real retornado."
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
            "description": "Tarefa de engenharia/codigo em background.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "prompt": {"type": "STRING", "description": "Instrucao tecnica detalhada"},
                    "description": {"type": "STRING", "description": "Resumo em 3 palavras"},
                },
                "required": ["prompt", "description"],
            },
        },
        {
            "name": "computer_use_action",
            "description": "Automacao visual (navegador, clique, screenshot).",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "description": "open_url, click, screenshot, etc."},
                    "target": {"type": "STRING", "description": "URL, seletor ou descricao"},
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

def _gcloud_access_token() -> Optional[str]:
    """Tenta `gcloud auth print-access-token` (Windows: gcloud.cmd)."""
    candidates = (["gcloud.cmd", "auth", "print-access-token"]
                  if os.name == "nt"
                  else ["gcloud", "auth", "print-access-token"])
    try:
        res = subprocess.run(candidates, capture_output=True, text=True,
                             check=True, timeout=10)
        token = res.stdout.strip()
        return token or None
    except Exception:
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
# Chamada à IA (única decisora)
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
               timeout: int = 15) -> Dict[str, Any]:
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


def route_via_ia(prompt: str, timeout: int = 15) -> Tuple[str, Dict[str, Any]]:
    """
    Roteia 100% via IA. Sem heurística local.
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
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
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
