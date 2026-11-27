# Bips e protocolos em banco próprio (BIP_DATABASE_URL)

Tira do banco principal as tabelas da **Emissão de Protocolo**: `bipagens` (cada bip) e
`protocolos_fatura` (os protocolos gerados, com o arquivo de cada um). Num projeto Neon
só delas, a leitura dos bips e protocolos ganha a própria cota gratuita de 5 GB/mês.

A tela continua consultando o principal para identificar o CT-e bipado (NF, tomador),
então ela ainda depende dele estar no ar. Sem a variável, tudo fica no principal.

Diferente do Painel pronto, **aqui há dados a copiar** (os bips pendentes e os
protocolos já gerados), e a cópia lê do banco principal: só dá para fazer com ele no ar.

## Passo a passo

Faça num momento calmo: **ninguém bipando nem gerando protocolo** entre os passos 4 e 6
(uns 10 minutos). Os números de protocolo continuam de onde pararam.

1. **Neon**: crie um projeto novo (grátis), por exemplo `bips`, na região
   *AWS São Paulo (sa-east-1)*. Copie a *connection string* (a com `-pooler` no host).
2. **GitHub** > Settings > Secrets and variables > Actions:
   - *New repository secret* `BIP_DATABASE_URL` = a connection string do passo 1.
   - Edite `ANTIGO_DATABASE_URL` e coloque o **mesmo valor do `DATABASE_URL` atual** (o
     banco principal de hoje -- é de lá que os bips vão ser copiados).
3. Confira se o banco principal está respondendo (o Painel abre).
4. **GitHub** > Actions > **Migrar do banco antigo** > *Run workflow*:
   - *tabelas*: `bipagens,protocolos_fatura` (**obrigatório**: sem isso ele recopiaria
     do principal tabelas que já saíram dele, como analistas e tratativas).
   - Se quiser conferir antes, rode primeiro com *simular* marcado.
5. **Vercel** > Settings > Environment Variables > *Add* `BIP_DATABASE_URL` (mesmo valor,
   *Production*) > Deployments > ⋯ > **Redeploy**.
6. Rode o passo 4 de novo (mesmas tabelas): pega o que tiver sido bipado entre um e outro.
   Pode repetir sem duplicar nada.
7. Abra a Emissão de Protocolo e confira os pendentes e os protocolos gerados. Na tela
   **Uso do banco**, a leitura dos bips passa a aparecer como **Emissão de Protocolo (bips)**.

Os bips antigos continuam no principal (não são apagados); depois de alguns dias com tudo
certo, podem ser removidos de lá.

## Voltar atrás

Apague `BIP_DATABASE_URL` da Vercel e faça Redeploy. **Atenção:** os bips e protocolos
feitos enquanto o banco próprio esteve ligado ficam só nele -- para não perdê-los, copie de
volta (Migrar do banco antigo com `ANTIGO_DATABASE_URL` = o banco dos bips e, no GitHub,
sem o secret `BIP_DATABASE_URL`, tabelas `bipagens,protocolos_fatura`).
