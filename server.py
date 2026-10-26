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
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Carrega .env do diretório do projeto se existir (não sobrescreve vars já exportadas)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

APP_HOST = os.getenv("JEV_PROXY_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("JEV_PROXY_PORT", "8001"))

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE}/chat/completions"

# Modelos padrão (podem ser sobrescritos por variáveis de ambiente)
MODEL_SIMPLE = os.getenv("MODEL_SIMPLE", "openai/gpt-4o-mini")
MODEL_INTERMEDIATE = os.getenv("MODEL_INTERMEDIATE", "qwen/qwen-2.5-coder-32b-instruct")
MODEL_COMPLEX = os.getenv("MODEL_COMPLEX", "deepseek/deepseek-r1")
MODEL_LONG_CONTEXT = os.getenv("MODEL_LONG_CONTEXT", "moonshotai/kimi-k3")
MODEL_VISION = os.getenv("MODEL_VISION", "openai/gpt-4o-mini")

JEV_ENDPOINT_URL = os.getenv("JEV_ENDPOINT_URL", "https://api.typesafe.ai/v1/decision")

app = FastAPI(title="jev-proxy", version="3.1.0")

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


def _estimate_local_complexity(prompt: str, has_image: bool = False) -> dict[str, Any]:
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
    client: httpx.AsyncClient, prompt: str, has_image: bool = False
) -> dict[str, Any]:
    """Consulta o motor TypeSafe Jev; em caso de falha, usa classificador local."""
    if has_image:
        return _estimate_local_complexity(prompt, has_image=True)

    api_key = os.getenv("TYPESAFE_API_KEY")
    if not api_key:
        return _estimate_local_complexity(prompt, has_image=False)

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

    return _estimate_local_complexity(prompt, has_image=False)


def _get_openrouter_api_key() -> str | None:
    """Sempre usa a chave do host/.env, ignorando Authorization dos clientes."""
    return os.getenv("OPENROUTER_API_KEY")


@app.get("/v1/models")
async def list_models():
    """Expõe os modelos que o proxy representa."""
    return {
        "object": "list",
        "data": [
            {
                "id": "typesafe-jev-decision",
                "object": "model",
                "created": 1700000000,
                "owned_by": "jev-proxy",
            },
            {
                "id": "jev-arbitro",
                "object": "model",
                "created": 1700000000,
                "owned_by": "jev-proxy",
            },
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy OpenAI com roteamento TypeSafe Jev para OpenRouter."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Corpo JSON inválido: {exc}")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="O campo 'messages' deve ser uma lista não vazia.")

    user_query = _extract_text(messages[-1].get("content", ""))
    has_image = _has_images(messages)

    async with httpx.AsyncClient() as client:
        decision = await _classify_with_jev(client, user_query, has_image=has_image)

    target_model = decision["target_model"]

    api_key = _get_openrouter_api_key()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY não configurada. Defina a variável de ambiente no host ou no arquivo .env.",
        )

    upstream_payload = dict(body)
    upstream_payload["model"] = target_model
    # Garante temperature padrão se ausente, mantendo compatibilidade com a função original
    upstream_payload.setdefault("temperature", 0.2)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "http://localhost:8001",
        "X-Title": "jev-proxy",
    }

    decision_headers = {
        "X-Jev-Tier": str(decision["tier"]),
        "X-Jev-Model": target_model,
        "X-Jev-Confidence": str(decision["confidence"]),
        "X-Jev-Reason": str(decision["reason"]),
    }

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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "jev-proxy"}


if __name__ == "__main__":
    import uvicorn
    print(f"[jev-proxy] Iniciando em {APP_HOST}:{APP_PORT}")
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_level="info")
