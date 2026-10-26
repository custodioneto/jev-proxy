# jev-proxy

Micro-servidor proxy/roteador inteligente de LLMs, compatível com a API OpenAI. Centraliza a função **TypeSafe Jev Complexity Router & Arbitrator** para ser usada tanto pelo [Open WebUI](https://openwebui.com/) quanto pelo [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness) ou qualquer outro cliente OpenAI-compatible.

## O que faz

Ao invés de apontar cada interface diretamente para um único modelo no OpenRouter, o `jev-proxy` expõe um único modelo — `typesafe-jev-decision` — que analisa o prompt do usuário e escolhe automaticamente o modelo mais adequado, considerando:

- complexidade do raciocínio;
- presença de código (especialmente C# / .NET);
- tamanho do contexto / arquitetura de projeto;
- presença de imagens (multimodal).

## Roteamento

| Tier | Gatilho | Modelo padrão |
|---|---|---|
| `SIMPLE` | Saudações, perguntas curtas, tarefas rotineiras | `openai/gpt-4o-mini` |
| `INTERMEDIATE_CODE` | Código, sintaxe, tarefas de desenvolvimento comum | `qwen/qwen-2.5-coder-32b-instruct` |
| `HIGH_COMPLEXITY` | Deadlock, concorrência, algoritmos, raciocínio profundo | `moonshotai/kimi-k3` |
| `LONG_CONTEXT` | Contextos longos, arquitetura, múltiplos arquivos | `moonshotai/kimi-k2.7-code` |
| `MULTIMODAL_VISION` | Mensagens contendo imagens | `openai/gpt-4o-mini` |

A classificação primeiro tenta consultar o motor **TypeSafe Jev** (`https://api.typesafe.ai/v1/decision`); se o endpoint falhar ou não estiver configurado, cai automaticamente para um classificador heurístico local determinístico.

## Tecnologias

- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [httpx](https://www.python-httpx.org/)
- [python-dotenv](https://github.com/theskumar/python-dotenv)
- [Nix Flakes](https://nixos.wiki/wiki/Flakes) (ambiente de desenvolvimento isolado)

## Estrutura

```
~/.local/share/jev-proxy/
├── flake.nix          # devShell Nix com Python 3.12 + dependências
├── flake.lock         # Lock do Nix
├── server.py          # Servidor FastAPI / proxy / roteador
├── .env               # CHAVES REAIS (não versionado)
├── .env.example       # Template de variáveis
├── .gitignore         # Garante que .env nunca suba para o repo
└── README.md          # Este arquivo
```

> ⚠️ **Nunca commit o arquivo `.env`.** Ele contém suas chaves privadas e já está listado no `.gitignore`.

## Instalação

1. Clone o repositório:

```bash
git clone https://github.com/custodioneto/jev-proxy.git ~/.local/share/jev-proxy
```

2. Copie o arquivo de exemplo e preencha com suas chaves:

```bash
cd ~/.local/share/jev-proxy
cp .env.example .env
# Edite .env com suas chaves do OpenRouter e TypeSafe
```

3. Crie o script de inicialização em `~/.local/bin`:

```bash
mkdir -p ~/.local/bin
cp run-jev-proxy ~/.local/bin/run-jev-proxy   # se o script estiver no repo
chmod +x ~/.local/bin/run-jev-proxy
```

## Configuração (.env)

```env
OPENROUTER_API_KEY=sk-or-v1-sua-chave-openrouter
TYPESAFE_API_KEY=vcp-sua-chave-typesafe

JEV_ENDPOINT_URL=https://api.typesafe.ai/v1/decision

MODEL_SIMPLE=openai/gpt-4o-mini
MODEL_INTERMEDIATE=qwen/qwen-2.5-coder-32b-instruct
MODEL_COMPLEX=moonshotai/kimi-k3
MODEL_LONG_CONTEXT=moonshotai/kimi-k2.7-code
MODEL_VISION=openai/gpt-4o-mini
```

## Como usar

### Iniciar o servidor

```bash
~/.local/bin/run-jev-proxy
```

O servidor sobe em `http://0.0.0.0:8001` e pode ser acessado tanto pelo host quanto por containers/sandboxes do Nix.

### Parar o servidor

```bash
pkill -f "python3 .*server.py"
```

### Testar com curl

Listar modelos:

```bash
curl http://127.0.0.1:8001/v1/models
```

Chat completion:

```bash
curl -X POST http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-dummy" \
  -d '{
    "model": "typesafe-jev-decision",
    "stream": false,
    "messages": [{"role": "user", "content": "Olá, tudo bem?"}]
  }'
```

Os headers de resposta incluem metadados da decisão:

- `X-Jev-Tier`
- `X-Jev-Model`
- `X-Jev-Confidence`
- `X-Jev-Reason`

## Integração

### DeepSeek Harness

- **Base URL:** `http://127.0.0.1:8001/v1`
- **Modelo:** `typesafe-jev-decision`
- **API Key:** `sk-dummy` (qualquer valor; o proxy usa a chave real do `.env`)

### Open WebUI

1. Vá em **Admin Settings → Connections → OpenAI API**.
2. **API URL:** `http://127.0.0.1:8001/v1`
3. **API Key:** `sk-dummy`
4. Salve. O modelo `TypeSafe Jev` (`typesafe-jev-decision`) aparecerá no seletor de chats.

## Segurança

- O proxy **ignora** o header `Authorization` enviado pelos clientes e usa sempre a `OPENROUTER_API_KEY` definida no host/`.env`.
- O arquivo `.env` está no `.gitignore` e nunca deve ser versionado.
- Em caso de commit acidental do `.env`, recomenda-se revogar e regenerar as chaves nos painéis oficiais.

## Licença

MIT
