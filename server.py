#!/usr/bin/env python3
"""
jev-proxy - Micro-servidor proxy/roteador inteligente para APIs de LLM.
Replica o comportamento da função Pipe "TypeSafe Jev Complexity Router & Arbitrator"
do Open WebUI, centralizando-a como um endpoint OpenAI-compatível.
"""

import os
import json
from typing import AsyncGenerator, Any

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Carrega .env do diretório do projeto se existir (não sobrescreve vars já exportadas)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

APP_HOST = os.getenv("JEV_PROXY_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("JEV_PROXY_PORT", "8001"))

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE}/chat/completions"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE}/models"

import time
_models_cache: dict[str, Any] = {"data": [], "ts": 0}
MODELS_CACHE_TTL = int(os.getenv("OPENROUTER_MODELS_CACHE_TTL", "60"))

# Modelos padrão (podem ser sobrescritos por variáveis de ambiente)
MODEL_SIMPLE = os.getenv("MODEL_SIMPLE", "google/gemini-flash-1.5")
MODEL_INTERMEDIATE = os.getenv("MODEL_INTERMEDIATE", "qwen/qwen-2.5-coder-32b-instruct")
MODEL_COMPLEX = os.getenv("MODEL_COMPLEX", "deepseek/deepseek-r1")
MODEL_LONG_CONTEXT = os.getenv("MODEL_LONG_CONTEXT", "moonshotai/kimi-k3")
MODEL_VISION = os.getenv("MODEL_VISION", "openai/gpt-4o-mini")
MODEL_TOOL_USE = os.getenv("MODEL_TOOL_USE", "moonshotai/kimi-k2.7-code")

JEV_ENDPOINT_URL = os.getenv("JEV_ENDPOINT_URL", "https://api.typesafe.ai/v1/decision")

app = FastAPI(title="jev-proxy", version="3.1.0")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Página inicial amigável com links para documentação e endpoints."""
    return """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>jev-proxy</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                max-width: 720px;
                margin: 4rem auto;
                padding: 0 1.5rem;
                line-height: 1.6;
                color: #e4e4e7;
                background: #18181b;
            }
            h1 { color: #22c55e; }
            a { color: #38bdf8; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .card {
                background: #27272a;
                border-radius: 0.75rem;
                padding: 1.5rem;
                margin-top: 1.5rem;
                border: 1px solid #3f3f46;
            }
            code {
                background: #3f3f46;
                padding: 0.2rem 0.4rem;
                border-radius: 0.375rem;
                font-size: 0.9em;
            }
            ul { padding-left: 1.25rem; }
            li { margin: 0.5rem 0; }
        </style>
    </head>
    <body>
        <h1>jev-proxy</h1>
        <p>Proxy/roteador inteligente OpenAI-compatível para APIs de LLM.</p>

        <div class="card">
            <h2>Documentação interativa</h2>
            <ul>
                <li><a href="/docs">Swagger UI (/docs)</a> — testar endpoints</li>
                <li><a href="/redoc">ReDoc (/redoc)</a> — documentação alternativa</li>
            </ul>
        </div>

        <div class="card">
            <h2>Endpoints principais</h2>
            <ul>
                <li><code>GET /v1/models</code> — listar modelos disponíveis</li>
                <li><code>POST /v1/chat/completions</code> — chat completions com roteamento TypeSafe Jev</li>
                <li><code>GET /health</code> — verificar saúde do serviço</li>
            </ul>
        </div>

        <div class="card">
            <h2>Integração</h2>
            <p><strong>Base URL:</strong> <code>http://127.0.0.1:8001/v1</code></p>
            <p><strong>Modelo:</strong> <code>jev-arbitro</code></p>
            <p><strong>API Key:</strong> qualquer valor (ex: <code>sk-dummy</code>)</p>
        </div>

        <p style="margin-top: 2rem; color: #71717a; font-size: 0.9rem;">
            O proxy ignora a chave enviada pelo cliente e usa a <code>OPENROUTER_API_KEY</code> configurada no host.
        </p>
    </body>
    </html>
    """

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SIMPLE_KEYWORDS = [
    "ola", "olá", "qual modelo", "quem e você", "quem é você",
    "teste", "ajuda", "bom dia", "boa tarde", "boa noite",
]

LONG_CONTEXT_KEYWORDS = [
    "arquitetura", "refatore o projeto", "analise os arquivos", "logs completos",
]

HIGH_COMPLEXITY_KEYWORDS = [
    "deadlock", "concorrencia", "concorrência", "benchmark",
    "complexidade assintotica", "complexidade assintótica", "race condition",
]


def _extract_text(content: Any) -> str:
    """Converte qualquer formato de content (string, lista de blocos ou None) para texto plano."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts).strip()
    return str(content or "")


def _has_images(messages: list[dict[str, Any]]) -> bool:
    """Identifica se alguma mensagem do histórico possui blocos de imagem."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in ("image_url", "image"):
                    return True
    return False


def _has_tools(payload: dict[str, Any]) -> bool:
    """Verifica se a requisição contém tools/functions/tool_choice."""
    return bool(
        payload.get("tools")
        or payload.get("functions")
        or payload.get("tool_choice") not in (None, "none", "")
    )


def _estimate_local_complexity(
    prompt: str, has_image: bool = False, has_tools: bool = False
) -> dict[str, Any]:
    """Classificador heurístico determinístico caso o endpoint remoto do Jev oscile."""
    if has_image:
        return {
            "tier": "MULTIMODAL_VISION",
            "target_model": MODEL_VISION,
            "confidence": 0.99,
            "reason": "Conteúdo com imagem detectado. Roteado para modelo com suporte a visão computacional.",
        }

    prompt_lower = prompt.lower()
    length = len(prompt)

    # Regra de segurança: mensagens com tools nunca usam o modelo SIMPLE,
    # mesmo que sejam saudações curtas. Preferência por modelos chineses de
    # boa qualidade e custo-benefício que suportem tool use.
    if has_tools:
        return {
            "tier": "TOOL_USE",
            "target_model": MODEL_TOOL_USE,
            "confidence": 0.95,
            "reason": "Requisição contém tools/functions. Roteado para modelo preferencial de tool use.",
        }

    # Regra 1: Saudações ou perguntas muito curtas
    if length < 80 and any(w in prompt_lower for w in SIMPLE_KEYWORDS):
        return {
            "tier": "SIMPLE",
            "target_model": MODEL_SIMPLE,
            "confidence": 0.98,
            "reason": "Consulta trivial/saudação identificada. Roteado para o modelo mais econômico em tokens.",
        }

    # Regra 2: Arquitetura e Contextos Longos
    if length > 2500 or any(w in prompt_lower for w in LONG_CONTEXT_KEYWORDS):
        return {
            "tier": "LONG_CONTEXT",
            "target_model": MODEL_LONG_CONTEXT,
            "confidence": 0.95,
            "reason": "Contexto extenso ou demanda de múltiplos arquivos. Roteado para o modelo de contexto longo.",
        }

    # Regra 3: Raciocínio pesado e Algoritmos densos
    if any(w in prompt_lower for w in HIGH_COMPLEXITY_KEYWORDS):
        return {
            "tier": "HIGH_COMPLEXITY",
            "target_model": MODEL_COMPLEX,
            "confidence": 0.92,
            "reason": "Problema de lógica/concorrência de alta complexidade. Roteado para raciocínio profundo.",
        }

    # Regra 4: Desenvolvimento de código comum / C#
    return {
        "tier": "INTERMEDIATE_CODE",
        "target_model": MODEL_INTERMEDIATE,
        "confidence": 0.90,
        "reason": "Tarefa de desenvolvimento/código padrão. Roteado para especialista em código.",
    }


async def _classify_with_jev(
    client: httpx.AsyncClient, prompt: str, has_image: bool = False, has_tools: bool = False
) -> dict[str, Any]:
    """Consulta o motor TypeSafe Jev; em caso de falha, usa classificador local."""
    if has_image:
        return _estimate_local_complexity(prompt, has_image=True, has_tools=has_tools)

    api_key = os.getenv("TYPESAFE_API_KEY")
    if not api_key:
        return _estimate_local_complexity(prompt, has_image=False, has_tools=has_tools)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "task": "routing_by_complexity",
        "context": prompt,
        "available_tiers": [
            {"tier": "SIMPLE", "model": MODEL_SIMPLE},
            {"tier": "INTERMEDIATE_CODE", "model": MODEL_INTERMEDIATE},
            {"tier": "HIGH_COMPLEXITY", "model": MODEL_COMPLEX},
            {"tier": "LONG_CONTEXT", "model": MODEL_LONG_CONTEXT},
        ],
    }

    try:
        resp = await client.post(JEV_ENDPOINT_URL, headers=headers, json=payload, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "tier": data.get("tier", "SIMPLE"),
                "target_model": data.get("selected_model", MODEL_SIMPLE),
                "confidence": data.get("confidence", 0.95),
                "reason": data.get("reason", "Decisão de complexidade validada pelo Jev."),
            }
    except Exception:
        pass

    return _estimate_local_complexity(prompt, has_image=False, has_tools=has_tools)


def _get_openrouter_api_key() -> str | None:
    """Sempre usa a chave do host/.env, ignorando Authorization dos clientes."""
    return os.getenv("OPENROUTER_API_KEY")


def _get_openrouter_headers() -> dict[str, str]:
    """Headers para chamadas ao OpenRouter."""
    return {
        "Authorization": f"Bearer {_get_openrouter_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "http://localhost:8001",
        "X-Title": "jev-proxy",
    }


async def _fetch_openrouter_models() -> list[dict[str, Any]]:
    """Busca modelos disponíveis no OpenRouter com cache TTL simples."""
    now = time.time()
    if now - _models_cache["ts"] < MODELS_CACHE_TTL:
        return _models_cache["data"]

    api_key = _get_openrouter_api_key()
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(OPENROUTER_MODELS_URL, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                _models_cache["data"] = models
                _models_cache["ts"] = now
                return models
    except Exception:
        pass

    return _models_cache["data"]


async def _forward_chat_to_openrouter(
    upstream_payload: dict[str, Any],
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse | JSONResponse:
    """Encaminha um chat completion para o OpenRouter, streaming ou não."""
    headers = _get_openrouter_headers()
    decision_headers = extra_headers or {}
    is_stream = bool(upstream_payload.get("stream", False))

    if is_stream:
        async def stream_generator() -> AsyncGenerator[str, None]:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    OPENROUTER_CHAT_URL,
                    headers=headers,
                    json=upstream_payload,
                ) as response:
                    if response.status_code != 200:
                        text = await response.aread()
                        yield f"data: {json.dumps({'error': text.decode()}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    async for chunk in response.aiter_text():
                        yield chunk

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                **decision_headers,
            },
        )

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            OPENROUTER_CHAT_URL,
            headers=headers,
            json=upstream_payload,
        )

    try:
        data = response.json()
    except Exception:
        data = {"error": response.text}

    return JSONResponse(
        content=data,
        status_code=response.status_code,
        headers=decision_headers,
    )


@app.get("/v1/models")
async def list_models():
    """Expõe jev-arbitro (roteador) e encaminha a lista de modelos do OpenRouter."""
    jev_models = [
        {
            "id": "jev-arbitro",
            "object": "model",
            "created": 1700000000,
            "owned_by": "jev-proxy",
        },
    ]

    or_models = await _fetch_openrouter_models()
    or_ids = {m.get("id") for m in or_models}

    merged = jev_models + [m for m in or_models if m.get("id") not in or_ids]

    return {
        "object": "list",
        "data": merged,
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy OpenAI com roteamento TypeSafe Jev ou encaminhamento direto para OpenRouter."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Corpo JSON inválido: {exc}")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="O campo 'messages' deve ser uma lista não vazia.")

    requested_model = body.get("model", "")

    # Se o cliente escolheu um modelo específico do OpenRouter, encaminha direto
    if requested_model != "jev-arbitro":
        upstream_payload = dict(body)
        upstream_payload.setdefault("temperature", 0.2)
        return await _forward_chat_to_openrouter(upstream_payload)

    # Roteamento TypeSafe Jev
    user_query = _extract_text(messages[-1].get("content", ""))
    has_image = _has_images(messages)
    has_tools = _has_tools(body)

    async with httpx.AsyncClient() as client:
        decision = await _classify_with_jev(client, user_query, has_image=has_image, has_tools=has_tools)

    target_model = decision["target_model"]

    api_key = _get_openrouter_api_key()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY não configurada. Defina a variável de ambiente no host ou no arquivo .env.",
        )

    upstream_payload = dict(body)
    upstream_payload["model"] = target_model
    upstream_payload.setdefault("temperature", 0.2)

    decision_headers = {
        "X-Jev-Tier": str(decision["tier"]),
        "X-Jev-Model": target_model,
        "X-Jev-Confidence": str(decision["confidence"]),
        "X-Jev-Reason": str(decision["reason"]),
    }

    return await _forward_chat_to_openrouter(upstream_payload, decision_headers)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "jev-proxy"}


if __name__ == "__main__":
    import uvicorn
    print(f"[jev-proxy] Iniciando em {APP_HOST}:{APP_PORT}")
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_level="info")
