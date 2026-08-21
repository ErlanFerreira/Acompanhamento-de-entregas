# Painel de Pendências de Entregas — Transrota

Aplicação web (FastAPI + Postgres) que sincroniza automaticamente com a API
"GW Serviços" e exibe um painel de pendências de entrega, com login por
usuário/senha e gestão de usuários.

## O que a aplicação faz

- Sincroniza com a API GW Serviços **automaticamente 4x ao dia** (horários
  configuráveis em `SYNC_TIMES`), gravando os CT-e num banco Postgres.
- Login por e-mail/senha, com dois papéis: **admin** (vê o Painel e gerencia
  usuários) e **usuário** (só vê o Painel).
- Sidebar com as abas **Painel** e **Gestão de Usuários** (esta última só
  visível para admins).
- No Painel, os filtros de data (inclusive "Personalizado…") deixam o próprio
  usuário consultar qualquer período dentro do que já foi sincronizado.
- Botão "Atualizar agora" para forçar uma sincronização imediata sem esperar
  o próximo horário agendado.

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

## Deploy no Railway

1. **Suba este projeto para um repositório no GitHub** (privado, recomendado
   — o `.env` já está no `.gitignore`, então nenhum segredo vai junto).
2. Em [railway.app](https://railway.app), crie um projeto novo e escolha
   **"Deploy from GitHub repo"**, apontando para esse repositório.
3. No mesmo projeto, clique em **"+ New" → "Database" → "Add PostgreSQL"**.
   O Railway injeta a variável `DATABASE_URL` automaticamente no serviço web
   assim que os dois estiverem no mesmo projeto (confira em "Variables" do
   serviço web se `DATABASE_URL` aparece referenciando o Postgres).
4. No serviço web, vá em **Variables** e adicione (sem aspas):

   | Variável | Valor |
   |---|---|
   | `SECRET_KEY` | uma string aleatória longa (ex.: gere com `python -c "import secrets; print(secrets.token_hex(32))"`) |
   | `GWSERVICOS_LOGIN` | mesmo valor do seu `.env` local (não commitado) |
   | `GWSERVICOS_SENHA` | mesmo valor do seu `.env` local (não commitado) |
   | `GWSERVICOS_GUID` | mesmo valor do seu `.env` local (não commitado) |
   | `SYNC_WINDOW_DAYS` | `90` (ou o que preferir) |
   | `SYNC_TIMES` | `06:10,11:10,15:10,19:10` (ou os horários que quiser) |
   | `ADMIN_EMAIL` | e-mail do primeiro administrador |
   | `ADMIN_PASSWORD` | senha do primeiro administrador (troque depois do primeiro login) |
   | `ADMIN_NOME` | nome do primeiro administrador |

5. O Railway detecta o `Procfile`/`railway.json` e sobe com
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT` automaticamente.
6. Depois do primeiro deploy, acesse a URL pública que o Railway gera, faça
   login com `ADMIN_EMAIL`/`ADMIN_PASSWORD`, e troque a senha (ou crie um
   novo admin e remova este) em **Gestão de Usuários**.
7. Cada `git push` no branch conectado dispara um novo deploy automático.

### Sobre custo

O Railway não tem mais um plano gratuito permanente — dá um crédito de teste
inicial e depois cobra a partir de ~US$5/mês (plano Hobby) pelo uso do
serviço web + Postgres. Isso foi uma escolha consciente (preferida a uma
combinação 100% gratuita com mais peças móveis) — ver conversa/handoff para
o contexto da decisão.

## Estrutura do projeto

```
app/
  main.py         rotas (login, painel, API de cargas, gestão de usuários)
  config.py       variáveis de ambiente
  db.py           conexão SQLAlchemy
  models.py       tabelas: users, cargas, meta
  security.py     hashing de senha (bcrypt), sessão via cookie assinado
  sync.py         autenticação + busca na API GW Serviços + upsert no banco
  scheduler.py    agendamento da sincronização (APScheduler)
  seed.py         cria as tabelas e o primeiro admin na primeira subida
templates/        HTML (Jinja2): base (sidebar), login, painel, usuarios
static/           CSS + JS (dashboard.js, usuarios.js, theme.js)
```

O histórico completo do projeto (incluindo como as credenciais da API GW
Serviços foram descobertas) está no `HANDOFF.md` guardado localmente — esse
arquivo tem valores sensíveis e não deve ser commitado neste repositório.
