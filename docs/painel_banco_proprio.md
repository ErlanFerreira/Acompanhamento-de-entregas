# Painel pronto em banco próprio (PAINEL_DATABASE_URL)

O **Painel pronto** é a lista de CT-e compactada que o Painel e o Comprovantes leem a
cada abertura (tabela `painel_snapshots`, ver `app/painel_snapshot.py`). É a leitura
mais frequente do app. Num projeto Neon só dele, ela ganha a própria cota gratuita de
5 GB/mês de transferência e deixa de gastar a do banco principal.

Não há nada a copiar: a sincronização regrava a tabela a cada rodada. Sem a variável,
tudo continua no banco principal, como antes.

## Passo a passo

1. **Neon**: crie um projeto novo (grátis), por exemplo `painel`, na região
   *AWS São Paulo (sa-east-1)*. Copie a *connection string* (a com `-pooler` no host).
   Não cole a senha em chat nem em arquivo do repositório.
2. **GitHub** > Settings > Secrets and variables > Actions > *New repository secret*:
   - Nome `PAINEL_DATABASE_URL`, valor = a connection string do passo 1.
3. **Vercel** > projeto > Settings > Environment Variables > *Add*:
   - Nome `PAINEL_DATABASE_URL`, mesmo valor, ambiente *Production*. Salve.
   - Deployments > ⋯ no último deploy > **Redeploy**.
4. **GitHub** > Actions > **sync** > *Run workflow* (ou "Atualizar agora" no Painel).
   No fim do log aparece o aviso "Painel pronto" com o tamanho de cada período.
5. Confira na tela **Uso do banco** (menu do supervisor): o consumo do Painel passa a
   aparecer na coluna **Painel pronto**, e o do **Principal** cai.

## Se faltar algum passo

- Variável só na Vercel (sem o secret no GitHub): o app procura o Painel pronto no banco
  novo, não acha e lê as linhas de CT-e do principal (funciona, mas gasta como antes).
- Variável só no GitHub: a sincronização grava no banco novo e o app continua lendo o
  Painel pronto antigo do principal até a variável chegar na Vercel.

Nos dois casos nada quebra; só não economiza até os dois lados estarem configurados.

## Voltar atrás

Apague `PAINEL_DATABASE_URL` da Vercel e do GitHub e faça Redeploy: o Painel pronto volta
a ser gravado e lido no principal na próxima sincronização.
