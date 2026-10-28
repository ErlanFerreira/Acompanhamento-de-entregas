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
- **Bipagem de CT-e** (`/bipagem`): bipa o código de barras (ou QR Code) do
  DACTE com um leitor USB. O número/série do CT-e saem da própria chave de
  acesso (44 dígitos, validada pelo dígito verificador); a NF e o CT-e do
  parceiro vêm do banco sincronizado. Cada bip fica gravado no banco
  (tabela `bipagens`), separado por finalidade:
  - **Envio de comprovantes** → "Controle de comprovantes do mês" (modelo
    `app/modelos/controle_comprovantes.xlsx`): todos os CT-e do mês de um
    tomador (raiz do CNPJ); os bipados saem com a data do bip em "Data de
    Envio ao Financeiro/Arquivo" e status "Recebido".
  - **Envio de faturas** → "Protocolo de faturas" (modelo
    `app/modelos/protocolo_envio.xlsx`): DACTE (CT-e do parceiro; sem
    redespacho, o nosso) + NF dos bipados, uma aba por filial + tomador —
    o recebedor é o tomador do CT-e. 82 CT-e por aba (1 página).
  - Dados do emitente (razão social/endereço por CNPJ de filial) ficam em
    `app/filiais.py`.

  De onde vem cada dado:
  - CT-e do parceiro = coluna "N. CT-e Redespacho" do relatório.
  - NF, série, filial e endereço do tomador = colunas do relatório cujo
    título tenha "Notas"/"NF", "Série", "Filial" e "Endereço
    Consignatário" — **precisam ser acrescentadas ao relatório
    personalizado "Pendências" no GW** (a sincronização detecta pelo nome,
    não precisa mexer no código).
  - Só acha CT-e dentro da janela sincronizada (`SYNC_WINDOW_DAYS`, 90 dias).

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

## Bot de emissão de CT-e (normal, cortesia, reentrega, devolução, diária, descarga, coleta/pallets, complementar, substituição)

Roda no seu PC, pelo terminal (`scripts/emitir_cte.py`, lógica em
`app/emissao_cte.py`). Você informa o tipo e a chave da NF-e; o bot entra
no GW, abre "Conhecimentos > Novo cadastro", escolhe o tipo e usa o próprio
"Localizar conhecimento…" do GW (pesquisa por "Chave Acesso NF-e") para
achar o CT-e original — o GW copia remetente, destinatário, tomador e notas.
Se a NF estiver em mais de um CT-e, ele pergunta qual usar. **Normal** e
**Cortesia** não têm CT-e original: o bot importa a nota pelo "Importar
XML/DANFE" do GW (procura no banco do GW e no servidor DF-e; a consulta
pública fica de fora porque pede captcha). Na **Devolução** a tela pergunta
se tem NFD: com NFD, o CT-e sai da própria NFD (importada como no Normal,
com o tipo Devolução, só com ela); sem NFD, pela chave da nota original o
bot acha o CT-e que está sendo devolvido e o GW inverte remetente e
destinatário.

O frete segue a regra do próprio GW:

| Tipo | Frete |
|---|---|
| Reentrega / Devolução | total do CT-e original × "% reentrega"/"% devolução" da tabela de preço do tomador (respeita o frete mínimo) |
| Substituição | mesmo valor do CT-e original |
| Normal / Cortesia | tabela de preço do tomador, calculada pelo GW ao importar a nota |
| Diária / Descarga / Coleta-Pallets / Complementar | a tabela não tem esse valor — o bot pergunta |

Se o tomador não tiver tabela, ou o % estiver zerado, o bot também pergunta
o valor. Antes de salvar mostra um resumo e pede confirmação. **Só salva —
nunca transmite à SEFAZ**; a transmissão continua manual, pelo GW.

```bash
python scripts/emitir_cte.py reentrega 26261009722463000131550060007711051171417923
python scripts/emitir_cte.py diaria <chave_nfe> --frete 150,00
python scripts/emitir_cte.py devolucao <chave_nfe> --simular   # faz tudo menos salvar
python scripts/emitir_cte.py descarga <chave_nfe> --ver        # mostra o navegador
```

Usa `PORTAL_EMAIL`/`PORTAL_SENHA` do `.env`. O GW só aceita uma sessão por
login, então quem estiver logado com o mesmo usuário no navegador cai.

### Pelo painel (tela "Emissão de CT-e")

O mesmo bot, sem terminal, como um assistente de perguntas e respostas
(`static/emissao.js`): que tipo de CT-e (com explicação de cada um), qual a
chave da NF-e (conferida enquanto digita), quem está pedindo. O workflow
`.github/workflows/emissao_cte.yml` roda em etapas
(`scripts/run_emissao_cte.py`), cada uma levando 1-3 min:

1. **preparar** — faz tudo no GW menos salvar. Se a NF estiver em mais de um
   CT-e, o assistente pergunta qual é o original e prepara de novo.
2. O assistente mostra o CT-e achado (remetente/destinatário/quem paga) e
   pergunta se usa o frete da tabela — ou pede o valor, quando o GW não tem.
   Depois mostra o resumo e pergunta se pode salvar.
3. **salvar** — refaz o lançamento com o CT-e e o frete confirmados e salva
   (sem transmitir). O assistente mostra o número do CT-e gravado.

Mesma NF e mesmo tipo: um pedido em andamento é retomado em vez de abrir
outro, e um CT-e já salvo só é emitido de novo se a pessoa confirmar. Os
pedidos ficam no banco separado (`CONSULTAS_DATABASE_URL`), como as consultas.

Quem pode usar: o supervisor e os analistas marcados em **Usuários →
"Pode emitir CT-e"**. O pedido do analista sai no nome dele (a tela não
pergunta) e ele vê só os pedidos dele em "Pedidos recentes".

O robô entra no GW com um usuário só dele; para saber no GW quem pediu, o
nome vai na observação do CT-e ("Solicitado por: Maria Silva (painel)", na
primeira linha livre depois da que o GW preenche) — sai também no DACTE.

As emissões rodam uma por vez (`concurrency` do workflow). Configuração:

- Criar no GW um **usuário só para o bot**, com permissão de lançar CT-e
  (assim ele não derruba a sessão de ninguém nem da sincronização).
- No GitHub, **Settings → Secrets and variables → Actions**: criar
  `GW_BOT_EMAIL` e `GW_BOT_SENHA` com esse login.
- O `GITHUB_DISPATCH_TOKEN` da Vercel já serve (o mesmo das Consultas).
- Se o usuário do robô tiver acesso de Colaborador em mais de uma
  organização, o GW pede pra escolher depois da senha: crie também o Secret
  `GW_BOT_ORGANIZACAO` com o CNPJ da certa (sem ele, o robô para avisando,
  em vez de escolher sozinho).

## Estrutura do projeto

```
app/
  main.py         rotas (login, painel, API de cargas)
  config.py       variáveis de ambiente
  db.py           conexão SQLAlchemy
  models.py       tabelas: cargas, meta
  security.py     sessão via cookie assinado (senha única, sem contas)
  scrape.py       automação de navegador (Playwright): login + gerar relatório
  emissao_cte.py  lança CT-e derivado (reentrega, devolução...) no GW, sem transmitir
  tipos_cte.py    tipos de CT-e do bot e validação da chave da NF-e (sem Playwright)
  sync.py         parseia o Excel do relatório e faz upsert no banco
  seed.py         cria as tabelas na primeira subida
templates/        HTML (Jinja2): base (sidebar), login, painel
static/           CSS + JS (dashboard.js, theme.js)
scripts/run_sync.py         rodado pelo GitHub Actions (sincronização periódica)
scripts/run_emissao_cte.py  rodado pelo GitHub Actions (etapas da tela Emissão de CT-e)
scripts/emitir_cte.py       roda no seu PC: emite CT-e a partir da chave da NF-e
.github/workflows/sync.yml  agendamento da sincronização (cron)
requirements.txt             dependências do app web (Vercel)
requirements-sync.txt        requirements.txt + openpyxl + playwright (só CI)
```

O histórico completo do projeto (incluindo como a limitação da API GW
Serviços foi descoberta e por que migramos para automação de navegador)
está no `HANDOFF.md` guardado localmente — esse arquivo tem valores
sensíveis e não deve ser commitado neste repositório.
