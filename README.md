# Painel de Pendências de Entregas — Transrota

Aplicação web (FastAPI + Postgres) que sincroniza automaticamente com o
sistema GW Sistemas e exibe um painel de pendências de entrega, atrás de
uma senha única compartilhada (sem contas de usuário).

## O que a aplicação faz

- Sincroniza com o relatório personalizado **"Pendências"** do portal
  Webtrans **automaticamente 4x ao dia**, via um workflow do GitHub Actions
  (`.github/workflows/sync.yml`) que automatiza login + geração do relatório
  num navegador headless, e grava os CT-e num banco Postgres.
- Acesso protegido por uma **senha única** (`PAINEL_SENHA`) — sem gestão de
  usuários/contas individuais.
- No Painel, os filtros de data (inclusive "Personalizado…") deixam o próprio
  usuário consultar qualquer período dentro do que já foi sincronizado.
- Botão "Atualizar agora" dispara uma sincronização fora do horário
  agendado (assíncrona — leva 1-2 minutos, não é instantânea).

### Por que via navegador automatizado, e não a API oficial

A API "GW Serviços" existe e autentica normalmente, mas só retorna cargas
onde o CNPJ logado é remetente ou destinatário — um acesso de "cliente", não
de transportadora. Comparamos direto: a API trazia ~12 CT-e/mês para uma
filial que na verdade emite +5.000/mês. A única forma encontrada de obter a
visão completa da empresa foi automatizar exatamente os cliques que um
usuário faria no portal Webtrans para gerar o relatório "Pendências" (login
normal, que tem acesso total). Ver `HANDOFF.md` para o histórico completo
dessa investigação.

## Arquitetura de hospedagem (100% gratuita)

- **Vercel** hospeda o app (FastAPI rodando como função serverless) — só as
  dependências leves (`requirements.txt`), sem o navegador automatizado.
- **Neon** é o banco Postgres (tier gratuito permanente).
- **GitHub Actions** roda a sincronização periódica (`scripts/run_sync.py`,
  com Playwright + Chromium — `requirements-sync.txt`), direto no banco,
  independente do app estar sendo acessado ou não. A Vercel não conseguiria
  rodar isso (função serverless não comporta um navegador Chromium
  instalado nem processos de fundo de longa duração).
- O botão "Atualizar agora" do painel dispara esse mesmo workflow via a API
  do GitHub (`workflow_dispatch`), não roda nada pesado dentro da Vercel.

## Rodando localmente

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # preencha com os valores reais (ver HANDOFF.md)
python -m uvicorn app.main:app --reload --port 8000
```

Sem `DATABASE_URL` preenchido, usa um arquivo SQLite local (`local.db`) —
suficiente para testar. O login usa a senha definida em `PAINEL_SENHA`
do `.env`.

Para testar a sincronização localmente (não precisa pra rodar só o app):

```bash
python -m pip install -r requirements-sync.txt
python -m playwright install chromium
python scripts/run_sync.py
```

## Deploy (Vercel + Neon + GitHub Actions)

### 1. Banco de dados (Neon)

1. Crie uma conta gratuita em [neon.tech](https://neon.tech) e um projeto novo.
2. No painel do projeto, copie a **connection string** — use a variante
   **pooled** (o host tem `-pooler` no nome), recomendada para funções
   serverless. Algo como:
   `postgresql://usuario:senha@ep-xxxx-pooler.sa-east-1.aws.neon.tech/neondb?sslmode=require`

### 2. App (Vercel)

1. Em [vercel.com](https://vercel.com), **Add New → Project** e importe o
   repositório `Acompanhamento-de-entregas` do GitHub.
2. A Vercel deve detectar o `vercel.json` automaticamente (runtime Python).
   Não precisa mudar build/output settings.
3. Em **Environment Variables**, adicione:

   | Variável | Valor |
   |---|---|
   | `DATABASE_URL` | a connection string pooled da Neon (passo 1) |
   | `SECRET_KEY` | uma string aleatória longa (gere com `python -c "import secrets; print(secrets.token_hex(32))"`) |
   | `SYNC_WINDOW_DAYS` | `90` |
   | `PAINEL_SENHA` | a senha para acessar o painel (compartilhe só com quem precisa) |
   | `GITHUB_REPO` | `usuario/nome-do-repositorio` (para o botão "Atualizar agora") |
   | `GITHUB_DISPATCH_TOKEN` | um PAT do GitHub com escopo `workflow` (passo 3.3) |

4. Clique em **Deploy**. Ao final, acesse a URL `*.vercel.app` gerada e
   entre com `PAINEL_SENHA`.
5. Cada `git push` no branch `main` dispara um novo deploy automático.

### 3. Sincronização automática (GitHub Actions)

1. No repositório GitHub, vá em **Settings → Secrets and variables →
   Actions → New repository secret** e crie:
   - `DATABASE_URL` (a mesma connection string da Neon)
   - `PORTAL_EMAIL`, `PORTAL_SENHA` (login normal do portal Webtrans — **não**
     as credenciais da API GW Serviços)
2. O workflow `.github/workflows/sync.yml` já está no repositório e roda
   sozinho nos horários definidos (padrão: 06:10, 11:10, 15:10 e 19:10,
   horário de Brasília). Para rodar manualmente a qualquer momento: aba
   **Actions** do repositório → "Sincronizar pendências" → **Run workflow**.
3. Para mudar os horários, edite o `cron:` em `.github/workflows/sync.yml`
   (horários em UTC = horário de Brasília + 3h) e faça commit/push.
4. Para o botão "Atualizar agora" funcionar: crie um token em
   [github.com/settings/tokens](https://github.com/settings/tokens) — um
   **fine-grained token** com acesso só a este repositório e permissão
   "Actions: Read and write" (ou um classic token com escopo `workflow`) — e
   configure como `GITHUB_DISPATCH_TOKEN` na Vercel (passo 2.3).

### Sobre custo

Essa combinação é gratuita indefinidamente para o volume de uso deste
painel: Vercel Hobby (sem custo), Neon tier gratuito (sem custo), GitHub
Actions (repositório privado tem 2000 minutos grátis/mês — a automação de
navegador é mais pesada que uma chamada de API simples, mas 4 execuções/dia
ainda ficam bem dentro do limite gratuito).

## Estrutura do projeto

```
app/
  main.py         rotas (login, painel, API de cargas)
  config.py       variáveis de ambiente
  db.py           conexão SQLAlchemy
  models.py       tabelas: cargas, meta
  security.py     sessão via cookie assinado (senha única, sem contas)
  scrape.py       automação de navegador (Playwright): login + gerar relatório
  sync.py         parseia o Excel do relatório e faz upsert no banco
  seed.py         cria as tabelas na primeira subida
templates/        HTML (Jinja2): base (sidebar), login, painel
static/           CSS + JS (dashboard.js, theme.js)
scripts/run_sync.py         rodado pelo GitHub Actions (sincronização periódica)
.github/workflows/sync.yml  agendamento da sincronização (cron)
requirements.txt             dependências do app web (Vercel)
requirements-sync.txt        requirements.txt + openpyxl + playwright (só CI)
```

O histórico completo do projeto (incluindo como a limitação da API GW
Serviços foi descoberta e por que migramos para automação de navegador)
está no `HANDOFF.md` guardado localmente — esse arquivo tem valores
sensíveis e não deve ser commitado neste repositório.
