# Interface Web (chat) — Text-to-Insight

Página web local, em formato de chat (estilo ChatGPT/Claude), para conversar com seu
banco SQLite em linguagem natural. Consome apenas a ponte pública
`text_to_insight.io_adapter` (`ask`/`resume`).

## Como rodar (uso normal)

Pré-requisitos: ambiente `textToInsight`, `.env` com `GOOGLE_API_KEY`, e o frontend
buildado uma vez.

```bash
# 1) buildar o frontend (só na primeira vez ou após mudar a UI)
cd web_interface/frontend
npm install
npm run build

# 2) subir o servidor (abre o navegador em http://localhost:8000)
conda run -n textToInsight t2i-web
```

## Funcionalidades (paridade com o CLI)

- Pergunta em linguagem natural → resposta + tabela + gráfico + download do CSV.
- **Pedir esclarecimento** (HITL): quando a pergunta é ambígua, o assistente pergunta
  antes de responder; você responde na própria caixa de chat.
- **Usar conhecimento extra** (RAG): liga/desliga no painel lateral.
- **Trocar banco**: dropdown com os `.db` da pasta `data/`.
- **Nova conversa**: limpa a memória e começa um novo tópico.

A memória da conversa vive no servidor (em RAM, por sessão/cookie) e é injetada na
pergunta, porque o engine não encadeia perguntas sozinho.

## Desenvolvimento (hot reload do front)

```bash
# terminal 1: backend
conda run -n textToInsight t2i-web        # API em :8000
# terminal 2: frontend com hot reload (proxy /api -> :8000)
cd web_interface/frontend && npm run dev   # abre :5173
```

## Variáveis de ambiente (opcionais)

- `T2I_WEB_HOST` (padrão `127.0.0.1`), `T2I_WEB_PORT` (padrão `8000`).
- `T2I_WEB_NO_BROWSER=1` para não abrir o navegador automaticamente.
