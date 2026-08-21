# Painel de Pendências de Entregas — Transrota

Aplicação web (FastAPI + Postgres) que sincroniza automaticamente com a API
"GW Serviços" e exibe um painel de pendências de entrega, com login por
usuário/senha e gestão de usuários.

## O que a aplicação faz

- Sincroniza com a API GW Serviços **automaticamente 4x ao dia** via um
  workflow do GitHub Actions (`.github/workflows/sync.yml`), gravando os
  CT-e num banco Postgres.
- Login por e-mail/senha, com dois papéis: **admin** (vê o Painel e gerencia
  usuários) e **usuário** (só vê o Painel).
- Sidebar com as abas **Painel** e **Gestão de Usuários** (esta última só
  visível para admins).
- No Painel, os filtros de data (inclusive "Personalizado…") deixam o próprio
  usuário consultar qualquer período dentro do que já foi sincronizado.
- Botão "Atualizar agora" para forçar uma sincronização imediata sem esperar
  o próximo horário agendado.

## Arquitetura de hospedagem (100% gratuita)

- **Vercel** hospeda o app (FastAPI rodando como função serverless).
- **Neon** é o banco Postgres (tier gratuito permanente).
- **GitHub Actions** dispara a sincronização periódica (`scripts/run_sync.py`)
  direto no banco, independente do app estar sendo acessado ou não —
  necessário porque a Vercel não mantém processos de fundo rodando entre
  requisições.

## Rodando localmente

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # preencha com os valores reais (ver HANDOFF.md)
python -m uvicorn app.main:app --reload --port 8000
```

Sem `DATABASE_URL` preenchido, usa um arquivo SQLite local (`local.db`) —
suficiente para testar. Na primeira subida, se não houver nenhum usuário no
banco, um admin é criado automaticamente com `ADMIN_EMAIL`/`ADMIN_PASSWORD`
do `.env`.

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
   | `GWSERVICOS_LOGIN` | mesmo valor do seu `.env` local (não commitado) |
   | `GWSERVICOS_SENHA` | mesmo valor do seu `.env` local (não commitado) |
   | `GWSERVICOS_GUID` | mesmo valor do seu `.env` local (não commitado) |
   | `SYNC_WINDOW_DAYS` | `90` |
   | `ADMIN_EMAIL` | e-mail do primeiro administrador |
   | `ADMIN_PASSWORD` | senha do primeiro administrador (troque depois do primeiro login) |
   | `ADMIN_NOME` | nome do primeiro administrador |

4. Clique em **Deploy**. Ao final, acesse a URL `*.vercel.app` gerada, faça
   login com `ADMIN_EMAIL`/`ADMIN_PASSWORD` e troque a senha em **Gestão de
   Usuários**.
5. Cada `git push` no branch `main` dispara um novo deploy automático.

### 3. Sincronização automática (GitHub Actions)

1. No repositório GitHub, vá em **Settings → Secrets and variables →
   Actions → New repository secret** e crie:
   - `DATABASE_URL` (a mesma connection string da Neon)
   - `GWSERVICOS_LOGIN`, `GWSERVICOS_SENHA`, `GWSERVICOS_GUID`
2. O workflow `.github/workflows/sync.yml` já está no repositório e roda
   sozinho nos horários definidos (padrão: 06:10, 11:10, 15:10 e 19:10,
   horário de Brasília). Para rodar manualmente a qualquer momento: aba
   **Actions** do repositório → "Sincronizar pendências" → **Run workflow**.
3. Para mudar os horários, edite o `cron:` em `.github/workflows/sync.yml`
   (horários em UTC = horário de Brasília + 3h) e faça commit/push.

### Sobre custo

Essa combinação é gratuita indefinidamente para o volume de uso deste
painel: Vercel Hobby (sem custo), Neon tier gratuito (sem custo), GitHub
Actions (repositório privado tem 2000 minutos grátis/mês — esta sincronização
usa uma fração disso).

## Estrutura do projeto

```
app/
  main.py         rotas (login, painel, API de cargas, gestão de usuários)
  config.py       variáveis de ambiente
  db.py           conexão SQLAlchemy
  models.py       tabelas: users, cargas, meta
  security.py     hashing de senha (bcrypt), sessão via cookie assinado
  sync.py         autenticação + busca na API GW Serviços + upsert no banco
  seed.py         cria as tabelas e o primeiro admin na primeira subida
templates/        HTML (Jinja2): base (sidebar), login, painel, usuarios
static/           CSS + JS (dashboard.js, usuarios.js, theme.js)
scripts/run_sync.py        rodado pelo GitHub Actions (sincronização periódica)
.github/workflows/sync.yml agendamento da sincronização (cron)
```

O histórico completo do projeto (incluindo como as credenciais da API GW
Serviços foram descobertas) está no `HANDOFF.md` guardado localmente — esse
arquivo tem valores sensíveis e não deve ser commitado neste repositório.
