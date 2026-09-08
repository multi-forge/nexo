# Nexo: Stack de MCPs Recomendados

## Critério de Seleção

Cada MCP foi avaliado por 3 eixos específicos do Nexo:

| Eixo | Pergunta |
|---|---|
| **Voice-First Utility** | O resultado é *falável*? Dá pra resumir em 10 segundos de áudio? |
| **Autonomia** | Reduz quantas vezes o Nexo precisa perguntar ao usuário? |
| **Latência** | É rápido o suficiente pra não travar o loop conversacional? |

---

##  Tier 1 — Essenciais (instalar no dia 1)

### 1. `@anthropic/mcp-git`
**O que faz:** Operações Git completas — diff, log, blame, branch, merge, stash.

**Por que é essencial:** O Nexo precisa de Git como um sentido primário. Sem ele, todo git passa pelo `execute_shell`, sem parsing estruturado.

**Exemplo de voz:**
>  *"O que mudou desde ontem?"*
>  *"12 commits na develop. Os maiores: refatoração do auth com 340 linhas alteradas, e a correção do webhook com 28. Quer o diff de algum?"*

```bash
agy mcp add git -- npx -y @anthropic/mcp-server-git --repository /path/to/repo
```

---

### 2. `context7`
**O que faz:** Busca documentação atualizada e versionada de qualquer biblioteca — direto da fonte, não alucinada.

**Por que é essencial:** O Nexo está gerando prompts para o AGY criar código. Se ele alucina uma API, o código quebra. Context7 elimina isso.

**Exemplo de voz:**
>  *"Como faz upload de arquivo no FastAPI?"*
>  *(busca docs do FastAPI via Context7, responde com a API real)*

```bash
agy mcp add context7 -- npx -y @upstash/context7-mcp@latest
```

---

### 3. `@anthropic/mcp-filesystem`
**O que faz:** Leitura/escrita segura de arquivos com escopo restrito a diretórios permitidos.

**Por que é essencial:** É a fundação. O Gemini Live precisa ler arquivos pra entender contexto antes de delegar ao AGY. Mais seguro que `cat` via shell.

**Exemplo de voz:**
>  *"Lê o arquivo de rotas pra mim, resumido."*
>  *"O routes/auth.py tem 4 endpoints: login, logout, refresh e register. Login e refresh usam JWT. Register valida email com regex."*

```bash
agy mcp add filesystem -- npx -y @anthropic/mcp-server-filesystem /home/user/project
```

---

### 4. `sequential-thinking`
**O que faz:** Raciocínio estruturado passo-a-passo com capacidade de revisar e ramificar hipóteses.

**Por que é essencial:** Voz é linear — o usuário não pode voltar e reler. O Nexo precisa pensar estruturadamente *antes* de falar pra não dar respostas confusas em decisões arquiteturais complexas.

**Exemplo de voz:**
>  *"Deveria usar Redis ou Postgres pra fila de jobs?"*
>  *(pensa estruturadamente, compara trade-offs)*
> *"Pra seu caso — volume baixo, sem infraestrutura Redis existente — recomendo Postgres com pg_boss. Menos uma dependência, e escala até 10 mil jobs por hora tranquilo. Se precisar de mais, migra pra Redis depois sem mudar a interface."*

```bash
agy mcp add sequential-thinking -- npx -y @anthropic/mcp-sequential-thinking
```

---

##  Tier 2 — Alto Valor (instalar na primeira semana)

### 5. `github`
**O que faz:** PRs, issues, reviews, actions status, notificações — tudo via API do GitHub.

**Por que é alto valor:** O Nexo pode abrir PR, responder review comments e checar CI/CD sem sair do fluxo de voz. Essencial pra quem trabalha em equipe.

**Exemplo de voz:**
>  *"Abre um PR da feature/avatar pra develop."*
>  *"PR aberto: 'feat: avatar upload endpoint'. 3 arquivos, 180 adições. CI rodando. Aviso quando o status sair."*

>  *"Tem algum review pendente?"*
>  *"Dois. O João pediu pra extrair a validação de imagem numa função separada no PR 47. E a Maria perguntou sobre o tamanho máximo do upload no PR 52."*

```bash
agy mcp add github -- npx -y @anthropic/mcp-server-github
# Requer: export GITHUB_PERSONAL_ACCESS_TOKEN="ghp_..."
```

---

### 6. `brave-search`
**O que faz:** Busca web completa — sites, stackoverflow, docs, artigos.

**Por que é alto valor:** O Nexo precisa pesquisar errors, comparar libs, e encontrar soluções sem que o usuário abra o navegador (impossível, tela no bolso).

**Exemplo de voz:**
>  *"Nexo, tô com esse erro: CORS policy blocked. O que pode ser?"*
>  *(busca no Brave)*
> *"O mais comum é falta do middleware de CORS no backend. No FastAPI, é uma linha: app.add_middleware(CORSMiddleware, allow_origins=['*']). Quer que eu adicione?"*

```bash
agy mcp add brave-search -- npx -y @anthropic/mcp-server-brave-search
# Requer: export BRAVE_API_KEY="BSA_..."
```

---

### 7. `memory` (Knowledge Graph persistente)
**O que faz:** Armazena e recupera conhecimento do projeto em um grafo persistente entre sessões.

**Por que é alto valor:** O Gemini Live reseta a cada ~8 minutos. O MCP Memory complementa o `session_state.json` com conhecimento de longo prazo: decisões arquiteturais, padrões do projeto, preferências do usuário.

**Exemplo de voz:**
>  *"Lembra que a gente decidiu usar UUID v7 em vez de auto-increment."*
>  *"Anotado. Vou usar UUID v7 como padrão em todos os novos models."*

> *(3 dias depois, sessão nova)*
>  *"Cria o model de produto."*
>  *(consulta memory, encontra: "usar UUID v7")*
> *"Criando Product com id UUID v7, como padrão do projeto..."*

```bash
agy mcp add memory -- npx -y @anthropic/mcp-server-memory
```

---

### 8. `docker`
**O que faz:** Gerenciamento de containers, images, volumes, compose — via linguagem natural.

**Por que é alto valor:** Subir, parar, debugar containers por voz é um superpoder. "Sobe o banco", "mostra os logs do Redis", "rebuild do container da API".

**Exemplo de voz:**
>  *"Sobe o stack do docker compose."*
>  *"Subindo... Postgres, Redis e API subiram em 12 segundos. O Nginx deu erro: porta 80 já em uso. Quer que eu mude pra 8080?"*

```bash
agy mcp add docker -- npx -y @anthropic/mcp-server-docker
```

---

##  Tier 3 — Nice-to-Have (instalar conforme necessidade)

### 9. `sentry`
**O que faz:** Monitora erros de produção, traces de performance, releases.

**Por que é útil:** Imagine estar caminhando e ouvir: *"Apareceu um erro novo em produção: NullPointerException no payment handler, 47 ocorrências na última hora. Quer que eu investigue?"*

```bash
agy mcp add sentry -- npx -y @anthropic/mcp-server-sentry
# Requer: export SENTRY_AUTH_TOKEN="sntrys_..."
```

---

### 10. `postgres` / `supabase`
**O que faz:** Queries SQL, inspeção de schema, migrations, análise de dados — via linguagem natural.

**Por que é útil:** *"Quantos usuários se cadastraram essa semana?"* → Query direto, sem abrir pgAdmin.

```bash
agy mcp add postgres -- npx -y @anthropic/mcp-server-postgres postgresql://user:pass@localhost/db
```

---

### 11. `todoist` / `linear`
**O que faz:** Gerenciamento de tasks e tickets via voz.

**Por que é útil:** O Nexo vira PM. *"Cria uma task pra corrigir o bug do login, prioridade alta."* → Ticket criado no board enquanto você caminha.

```bash
agy mcp add todoist -- npx -y @anthropic/mcp-server-todoist
# Requer: export TODOIST_API_TOKEN="..."
```

---

### 12. `telegram` (MCP, não só logger)
**O que faz:** Bidirecional — não só envia logs, mas também lê mensagens, grupos, e pode agir como interface alternativa de texto.

**Por que é útil:** Quando a voz não é possível (reunião, ônibus lotado), o Telegram vira fallback. O usuário digita no Telegram, o Nexo responde lá E atualiza o estado da sessão de voz.

```bash
agy mcp add telegram -- npx -y @anthropic/mcp-server-telegram
# Requer: export TELEGRAM_BOT_TOKEN="..."
```

---

## Arquitetura Integrada

```
                         NEXO
                     ┌──────────────┐
                     │  Gemini Live │
                     │   Session    │
                     └──────┬───────┘
                            │
              ┌─────────────┼─────────────────┐
              ▼             ▼                 ▼
        [Fast Path]    [Code Path]      [Deep Path]
              │             │                 │
    ┌─────────┴──────┐      │          ┌──────┴──────┐
    ▼                ▼      ▼          ▼             ▼
┌────────┐   ┌──────────┐ ┌───┐  ┌─────────┐  ┌──────────┐
│  MCPs  │   │ execute  │ │AGY│  │ AGY via  │  │ Telegram │
│ Tier 1 │   │  _shell  │ │   │  │ stream-  │  │  Logger  │
└───┬────┘   └──────────┘ └───┘  │  json    │  └──────────┘
    │                             └──────────┘
    ├── git ·········· diff, blame, log, branch
    ├── filesystem ··· read/write seguro
    ├── context7 ····· docs atualizadas
    ├── sequential ··· raciocínio estruturado
    │
    ├── github ······· PRs, issues, CI status
    ├── brave-search · pesquisa web
    ├── memory ······· conhecimento persistente
    ├── docker ······· containers
    │
    ├── sentry ······· erros de produção
    ├── postgres ····· queries diretas
    ├── todoist ······· task management
    └── telegram ····· interface fallback
```

## Script de Instalação Completa

```bash
#!/bin/bash
# nexo-mcp-setup.sh — Instala todos os MCPs do Nexo

set -euo pipefail

echo " Instalando MCPs Tier 1 (Essenciais)..."

agy mcp add git -- npx -y @anthropic/mcp-server-git \
    --repository "${PROJECT_DIR:-.}"

agy mcp add context7 -- npx -y @upstash/context7-mcp@latest

agy mcp add filesystem -- npx -y @anthropic/mcp-server-filesystem \
    "${PROJECT_DIR:-.}"

agy mcp add sequential-thinking -- npx -y @anthropic/mcp-sequential-thinking

echo " Instalando MCPs Tier 2 (Alto Valor)..."

agy mcp add github -- npx -y @anthropic/mcp-server-github

agy mcp add brave-search -- npx -y @anthropic/mcp-server-brave-search

agy mcp add memory -- npx -y @anthropic/mcp-server-memory

agy mcp add docker -- npx -y @anthropic/mcp-server-docker

echo " MCPs essenciais instalados."
echo ""
echo "Para Tier 3, instale manualmente conforme necessidade:"
echo "  agy mcp add sentry -- npx -y @anthropic/mcp-server-sentry"
echo "  agy mcp add postgres -- npx -y @anthropic/mcp-server-postgres \$DATABASE_URL"
echo "  agy mcp add todoist -- npx -y @anthropic/mcp-server-todoist"
echo "  agy mcp add telegram -- npx -y @anthropic/mcp-server-telegram"
```

> [!TIP]
> **Ordem de impacto no Nexo por voz:**
> 1. **memory** — resolve o problema de reset de sessão de 8 min
> 2. **context7** — elimina alucinações de API nos prompts do AGY
> 3. **git** — Git estruturado > git via shell
> 4. **brave-search** — pesquisa sem tela é game-changer
> 5. **github** — PR por voz é o fluxo mais satisfatório

> [!WARNING]
> Cada MCP é um processo separado consumindo memória. No host, com 12 MCPs rodando,
> espere ~200-400 MB de overhead. Se o host for limitado, comece só com Tier 1
> e adicione sob demanda.
