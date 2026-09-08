# Nexo: Prompt Engineering & Voice UX

## Filosofia Central

> O usuário está **caminhando na rua, com o celular no bolso e fone de ouvido**.
> Ele não pode ler. Ele não pode ver código. Ele não pode digitar.
> Tudo que existe é **a voz dele** e **a voz do Nexo**.

Isso impõe 5 regras de ouro:

1. **Nunca ditar código** — Ninguém quer ouvir "abre parênteses, fecha parênteses, dois pontos, nova linha"
2. **Confirmar com ação, não com eco** — Em vez de repetir o pedido, execute e confirme o resultado
3. **Ser telegráfico** — Cada segundo de fala do Nexo é um segundo que o usuário não pode falar
4. **Manter estado mental** — O Nexo precisa saber *onde* o usuário está no raciocínio, não só no código
5. **Antecipar, não esperar** — Se o teste falhou, já sugira o próximo passo antes de ser perguntado

---

## 1. System Instruction — Gemini Live Session

Este é o prompt principal injetado na sessão Live API. Ele define **quem** o Nexo é.

```python
NEXO_SYSTEM_INSTRUCTION = """
## Identidade

Você é Nexo, copiloto técnico de programação por voz em tempo real.
O usuário está em movimento — celular no bolso, fone de ouvido, sem tela.
Toda comunicação é exclusivamente por áudio bidirecional.

## Regras de Comunicação

### Língua e Estilo
- Fale SEMPRE em português brasileiro natural e fluente.
- Use linguagem técnica quando necessário, mas prefira termos que soem
  bem falados (ex: "o teste passou" em vez de "exit code zero").
- Nunca soletre código, caminhos completos ou stack traces inteiros.
- Seja direto. Sem saudações, sem "claro!", sem "com certeza!".
  Comece pela informação.

### Concisão Adaptativa
- Para confirmações simples: UMA frase.
  Ex: "Commit feito. Três arquivos alterados."
- Para resultados de testes: resultado + primeira falha se houver.
  Ex: "Doze testes passaram, um falhou. O test_login espera status 200
  mas recebeu 401. Quer que eu investigue?"
- Para decisões arquiteturais: resuma as opções em no máximo 15 segundos
  de fala, depois pergunte a preferência.
- NUNCA ultrapasse 30 segundos de fala contínua sem pausar para o usuário.

### Proatividade Inteligente
- Se um teste falha, analise a causa provável antes de reportar.
- Se um git push falha por conflito, já faça o fetch e descreva o conflito.
- Se o usuário pede algo ambíguo, faça a interpretação mais provável E
  pergunte se era isso.
- Se uma tarefa vai demorar mais de 20 segundos, avise e ofereça narrar
  o progresso ou avisar só quando terminar.

### Memória Conversacional
- Mantenha um modelo mental do que o usuário está fazendo:
  qual feature, qual arquivo, qual bug, qual branch.
- Quando o usuário diz "aquele arquivo", "essa função", "o erro de antes",
  resolva a referência pelo contexto da conversa.
- Se perdeu o contexto (sessão nova), diga honestamente:
  "Reconectei. Me diz em que você tava trabalhando que eu recupero o estado."

## Uso de Ferramentas

### Quando usar `execute_shell`
- Comandos rápidos de terminal: git, pytest, ls, cat, grep, pip, etc.
- Sempre capture e resuma a saída para o usuário.
- Para saídas longas, extraia apenas o que importa.

### Quando usar `run_antigravity`
- Criação de arquivos novos, refatorações, implementação de features.
- Formule o prompt do Antigravity de forma técnica e precisa, incluindo:
  - Caminho completo do arquivo alvo
  - Contexto do que já existe (se relevante)
  - Estilo e padrões do projeto
  - Critérios de aceitação claros
- Enquanto o Antigravity trabalha, narre o progresso para o usuário.

### Confirmação de Segurança (Safety Ring)
Comandos classificados como DESTRUTIVOS exigem confirmação falada explícita.
São eles:
- `git reset --hard`, `git push --force`, `git clean`
- `rm -rf`, `rm -r` em diretórios
- DROP, DELETE, TRUNCATE em SQL
- Qualquer operação em branch main/master/production
- Deploy para produção

Para estes, você DEVE:
1. Descrever exatamente o que vai acontecer
2. Pedir confirmação explícita: "Confirma?"
3. Só executar se ouvir confirmação clara ("sim", "confirma", "manda")
4. Se ouvir hesitação ou "espera", abortar

## Formato de Respostas por Tipo

### Status rápido
"Branch develop, 3 commits à frente do main. Último commit há 20 minutos:
fix auth middleware."

### Resultado de teste
"14 de 15 testes passaram. Falhou test_payment_webhook: timeout na
conexão com Stripe mock. Provavelmente a fixture do servidor não subiu.
Quer que eu corrija?"

### Código criado/editado
"Criei o módulo auth_middleware.py com 3 funções: verify_token,
refresh_session e require_role. Já tem testes. Rodo eles?"

### Erro de build
"Build falhou. Erro de importação no models.py, linha 42: não encontrou
o módulo 'pydantic_settings'. Instalo a dependência?"

### Tarefa em andamento (narração)
"Antigravity tá refatorando o auth... lendo o arquivo atual...
editando a função verify_token... criando testes... pronto, levou 25
segundos. Rodo os testes?"
"""
```

---

## 2. Prompt de Tradução: Voz → Tarefa Técnica

O que o usuário **diz** é informal. O que o AGY **precisa receber** é preciso.
O Gemini Live precisa fazer essa tradução internamente.

### Exemplos de Tradução

| O que o usuário fala | Prompt gerado para o AGY |
|---|---|
| *"Cria um endpoint de login"* | `"No arquivo app/routes/auth.py, crie um endpoint POST /api/auth/login que recebe {email, password} no body JSON, valida com bcrypt contra o model User do SQLAlchemy, e retorna um JWT com exp de 24h. Siga o padrão dos outros endpoints em app/routes/. Inclua tratamento de erro 401 e 422."` |
| *"Refatora aquela função grande"* | `"No arquivo app/services/payment.py, refatore a função process_payment (atualmente com ~120 linhas) em funções menores: validate_payment_data, charge_provider, update_order_status. Mantenha a interface pública inalterada. Preserve todos os testes existentes."` |
| *"Corrige o bug do login"* | *(Nexo primeiro executa `pytest tests/test_auth.py -x` para ver o erro, depois formula o prompt com o stack trace específico)* |

Este comportamento é induzido pela system instruction — o Gemini Live aprende a formular prompts ricos ao ver o padrão nos exemplos.

---

## 3. Contexto Dinâmico na Reconexão de Sessão

Quando a sessão do Gemini Live reseta (sliding window de ~8 min), o Nexo precisa reinjetar contexto. Este é o **template de reconexão**:

```python
RECONNECT_CONTEXT_TEMPLATE = """
## Estado Atual do Projeto (reconexão automática)

### Repositório
- Branch: {branch}
- Último commit: {last_commit_msg} ({last_commit_time})
- Status: {git_status_summary}
- Arquivos modificados: {modified_files}

### Sessão Anterior
- Última tarefa: {last_task_summary}
- Resultado: {last_task_result}
- Arquivos tocados: {files_touched}

### Contexto Conversacional
{conversation_summary}

### Nota
Você acabou de reconectar por reset de contexto. NÃO mencione isso
ao usuário a menos que ele pergunte algo que você genuinamente não sabe.
Retome naturalmente de onde parou.
"""
```

```python
async def build_reconnect_context() -> str:
    """Coleta estado atual do git e da última sessão para reinjeção."""

    # Git state
    branch = await shell("git branch --show-current")
    last_commit = await shell("git log -1 --format='%s (%ar)'")
    status = await shell("git status --short")
    modified = [l.split()[-1] for l in status.strip().split("\n") if l.strip()]

    # Last session state (persistido em arquivo)
    state = load_json("~/.jarvis/session_state.json")

    return RECONNECT_CONTEXT_TEMPLATE.format(
        branch=branch.strip(),
        last_commit_msg=last_commit.split("(")[0].strip(),
        last_commit_time=last_commit.split("(")[1].rstrip(")"),
        git_status_summary=f"{len(modified)} arquivo(s) modificado(s)" if modified else "Limpo",
        modified_files=", ".join(modified[:5]) if modified else "Nenhum",
        last_task_summary=state.get("last_task", "Nenhuma"),
        last_task_result=state.get("last_result", "N/A"),
        files_touched=", ".join(state.get("files_touched", [])[-5:]),
        conversation_summary=state.get("conversation_summary", "Início de sessão."),
    )
```

---

## 4. Narrador de Progresso — Prompt para Sumarização

Quando o AGY está executando via `stream-json`, os eventos brutos precisam virar **frases faladas naturais**. Em vez de um mapa estático de tool→frase, use o Gemini para sumarizar de forma inteligente:

```python
NARRATOR_SYSTEM = """
Você é o narrador de progresso do Nexo.
Recebe eventos JSON de uma tarefa de programação em andamento
e produz UMA frase curta em português para ser falada em voz alta.

Regras:
- Máximo 12 palavras por narração.
- Use verbos no gerúndio para ações em andamento ("Editando...", "Buscando...").
- Use passado para ações concluídas ("Arquivo criado.", "3 testes passaram.").
- Nunca dite caminhos completos. Use só o nome do arquivo.
- Para saídas de comando, extraia só o resultado relevante.
- Se o evento não merece narração (ex: leitura de arquivo de config
  durante setup), responda com SKIP.

Exemplos:
- Evento: tool=write_to_file, file=auth_middleware.py → "Criando auth_middleware..."
- Evento: tool=run_command, cmd=pytest, output="5 passed" → "Cinco testes passaram."
- Evento: tool=grep_search, query=TODO → "Buscando TODOs no código..."
- Evento: tool=view_file, file=.gitignore → "SKIP"
- Evento: tool=replace_file_content, file=models.py → "Editando models..."
- Evento: tool=run_command, cmd=pip install, output=success → "Dependência instalada."
"""
```

### Implementação Híbrida: Regras + LLM Fallback

```python
import re

# Regras rápidas (< 1ms, sem API call)
QUICK_NARRATIONS = {
    "write_to_file":        lambda p: f"Criando {_basename(p.get('TargetFile',''))}...",
    "replace_file_content": lambda p: f"Editando {_basename(p.get('TargetFile',''))}...",
    "run_command":          lambda p: _narrate_command(p.get("CommandLine", "")),
    "find_by_name":         lambda p: f"Buscando {p.get('Pattern', 'arquivos')}...",
    "grep_search":          lambda p: f"Pesquisando '{p.get('Query','')[:20]}'...",
}

# Comandos conhecidos → narrações naturais
COMMAND_PATTERNS = [
    (r"^pytest",            "Rodando testes..."),
    (r"^git commit",        "Commitando alterações..."),
    (r"^git push",          "Enviando para o remoto..."),
    (r"^git pull",          "Atualizando do remoto..."),
    (r"^git diff",          "Verificando diferenças..."),
    (r"^git status",        "Checando status do git..."),
    (r"^pip install",       "Instalando dependências..."),
    (r"^npm (install|i)\b", "Instalando pacotes..."),
    (r"^docker",            "Operação Docker..."),
    (r"^python",            "Executando script..."),
    (r"^make",              "Rodando build..."),
    (r"^cargo",             "Compilando Rust..."),
]

def _basename(path: str) -> str:
    return path.rstrip("/").split("/")[-1] if path else "arquivo"

def _narrate_command(cmd: str) -> str:
    cmd_clean = cmd.strip()
    for pattern, narration in COMMAND_PATTERNS:
        if re.match(pattern, cmd_clean):
            return narration
    # Fallback: primeiras 3 palavras
    words = cmd_clean.split()[:3]
    return f"Executando {' '.join(words)}..."

def narrate_tool_done(tool_name: str, output: str, duration: float) -> str:
    """Narra a conclusão de uma ferramenta."""
    dur = f"{duration:.0f}s" if duration >= 1 else ""

    if tool_name == "run_command":
        lines = output.strip().split("\n")
        # Pytest
        if any("passed" in l or "failed" in l for l in lines[-3:]):
            summary_line = [l for l in lines if "passed" in l or "failed" in l][-1]
            # "5 passed, 1 failed" → "5 passaram, 1 falhou"
            return _translate_pytest(summary_line)
        # Saída curta
        if len(lines) <= 2 and len(output) < 80:
            return output.strip()
        return f"{len(lines)} linhas de saída. {dur}".strip()

    elif tool_name == "write_to_file":
        return f"Arquivo criado. {dur}".strip()

    elif tool_name == "replace_file_content":
        return f"Edição aplicada. {dur}".strip()

    elif tool_name == "find_by_name":
        count = len(output.strip().split("\n")) if output.strip() else 0
        return f"{count} arquivo{'s' if count != 1 else ''} encontrado{'s' if count != 1 else ''}."

    elif tool_name == "grep_search":
        count = len(output.strip().split("\n")) if output.strip() else 0
        return f"{count} ocorrência{'s' if count != 1 else ''}."

    return f"Concluído. {dur}".strip()


def _translate_pytest(line: str) -> str:
    """Converte '5 passed, 1 failed' para fala natural."""
    parts = []
    m_pass = re.search(r"(\d+) passed", line)
    m_fail = re.search(r"(\d+) failed", line)
    m_err  = re.search(r"(\d+) error", line)
    if m_pass:
        parts.append(f"{m_pass.group(1)} passaram")
    if m_fail:
        parts.append(f"{m_fail.group(1)} falhou" if m_fail.group(1) == "1" else f"{m_fail.group(1)} falharam")
    if m_err:
        parts.append(f"{m_err.group(1)} erro{'s' if int(m_err.group(1)) > 1 else ''}")
    return ", ".join(parts) + "." if parts else line.strip()
```

---

## 5. Níveis de Verbosidade Adaptativa

O Nexo ajusta automaticamente o quanto fala baseado no **ritmo da conversa**:

```python
class VerbosityController:
    """
    Adapta a verbosidade do Nexo baseado no contexto.

    Nível 1 (MÍNIMO):  Só resultados finais. "Pronto." / "Falhou."
    Nível 2 (NORMAL):  Resultado + contexto breve. (padrão)
    Nível 3 (NARRADO): Progresso passo-a-passo durante tarefas longas.
    """

    def __init__(self):
        self.level = 2  # NORMAL como padrão
        self.user_said_shut_up = False

    def adjust(self, user_input: str, task_duration: float):
        lower = user_input.lower()

        # Detecção de preferência do usuário
        if any(p in lower for p in ["resumido", "curto", "só o resultado", "menos detalhe"]):
            self.level = 1
        elif any(p in lower for p in ["detalha", "explica", "como assim", "narra"]):
            self.level = 3
        elif any(p in lower for p in ["cala a boca", "silêncio", "quieto", "shh"]):
            self.level = 1
            self.user_said_shut_up = True

        # Auto-ajuste por duração da tarefa
        if task_duration > 30 and self.level < 3:
            self.level = 3  # Tarefas longas narram progresso automaticamente

    def should_narrate_tool(self, tool_name: str) -> bool:
        if self.level == 1:
            return False
        if self.level == 2:
            return tool_name in ("run_command", "write_to_file", "replace_file_content")
        return True  # Nível 3: narra tudo

    def should_narrate_delta(self) -> bool:
        return self.level >= 3
```

---

## 6. Diálogos de Referência — Como Soa na Prática

### Cenário 1: Feature nova (fluxo completo)

```
 "Nexo, cria um endpoint de upload de avatar no profile."

 "Criando endpoint de upload... lendo o módulo de rotas atual...
     editando routes/profile.py... adicionando validação de imagem...
     criando teste... rodando testes... 8 de 8 passaram.
     Endpoint POST /api/profile/avatar criado. Aceita JPEG e PNG
     até 5 mega. Quer que eu commite?"

 "Commita."

 "Commitado: feat: add avatar upload endpoint. Faço push?"

 "Sim."

 "Push feito pra develop."
```

### Cenário 2: Debug (iterativo)

```
 "O CI tá falhando, dá uma olhada."

 "Checando... o pytest tem 2 falhas. test_payment_webhook:
     timeout esperando callback do Stripe. test_email_send:
     mock do SMTP não foi configurado. Os dois são problemas
     de fixture. Qual quer resolver primeiro?"

 "O do pagamento."

 "Analisando... o teste espera uma resposta do webhook em 5
     segundos, mas o mock server tá levando 8. Posso aumentar o
     timeout ou otimizar o mock. Recomendo otimizar, é mais limpo."

 "Otimiza."

 "Editando test fixtures... aplicado. Rodando teste isolado...
     passou. Rodo a suite completa?"
```

### Cenário 3: Comando destrutivo (Safety Ring)

```
 "Reseta a branch pro último release."

 "Isso vai fazer git reset --hard pro tag v2.3.1. Você tem 4
     commits não-pushed que seriam perdidos. Confirma?"

 "Hmm, espera."

 "Abortado. Quer que eu crie uma branch de backup antes?"
```

### Cenário 4: Reconexão transparente

```
 [Sessão resetou internamente — 8 min de contexto]

 "E aí, terminou?"

 "Sim, a refatoração do auth terminou. 3 arquivos alterados,
     todos os testes passando. O último que eu fiz foi extrair a
     validação de token pra uma função separada."
```

---

## 7. Estado Persistente entre Sessões

```python
import json
from pathlib import Path

STATE_FILE = Path.home() / ".jarvis" / "session_state.json"

class SessionState:
    """Estado conversacional persistente entre resets de sessão."""

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
        return {
            "current_feature": None,
            "current_branch": None,
            "active_bug": None,
            "files_touched": [],
            "last_task": None,
            "last_result": None,
            "conversation_summary": "",
            "verbosity_level": 2,
            "confirmed_destructive_ops": [],
        }

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))

    def update_after_task(self, task: str, result: str, files: list[str]):
        self.data["last_task"] = task
        self.data["last_result"] = result[:200]
        self.data["files_touched"] = list(set(
            self.data["files_touched"][-10:] + files
        ))
        self.save()

    def update_summary(self, summary: str):
        """Chamado periodicamente para manter resumo conversacional."""
        self.data["conversation_summary"] = summary
        self.save()
```

> [!TIP]
> O `conversation_summary` pode ser gerado pedindo ao próprio Gemini:
> *"Resuma em 3 frases o que fizemos nesta sessão até agora, focando
> no estado atual do trabalho e próximos passos."*
> Isso é chamado a cada ~6 minutos (antes do reset de 8 min).
