# Emissão de CT-e em banco próprio (EMISSAO_DATABASE_URL)

As tabelas da emissão (`emissoes_cte`, `emissao_robos`, `emissao_leituras`) saem do banco
separado (o das consultas e do login dos analistas) e vão para um projeto Neon só delas.
Motivo: a tela acompanha o lote a cada poucos segundos e o robô do PC consulta a fila o dia
todo; isso gastava a cota de transferência do banco que também guarda o login.

Sem a variável `EMISSAO_DATABASE_URL`, nada muda (as tabelas ficam onde estão). Por isso dá
para publicar o código antes e trocar a configuração depois, e para voltar atrás é só tirar
a variável.

## Passo a passo

1. **Neon**: *New project*, nome `emissao`, região **AWS South America East 1 (São Paulo)**.
   Em *Connect*, copie a connection string (com *Connection pooling* ligado).
2. **GitHub** > Settings > Secrets and variables > Actions > *New repository secret*:
   - `EMISSAO_DATABASE_URL` = a string do passo 1.
   - `ANTIGO_DATABASE_URL` = o valor atual de `CONSULTAS_DATABASE_URL` (o banco de onde os
     pedidos saem; o valor antigo desse secret não é mais necessário).
3. **Copiar os pedidos**: Actions > *Migrar do banco antigo* > Run workflow, primeiro com
   *simular* marcado. Ele copia só as tabelas de emissão (as outras aparecem como "mesmo
   banco" ou "não existe") e mantém os mesmos ids. Pode rodar de novo sem duplicar. Rode de
   verdade desmarcando *simular*. Faça fora de um lote em andamento.
4. **Vercel** > Settings > Environment Variables: `EMISSAO_DATABASE_URL` = a mesma string;
   depois *Redeploy*.
5. **Robô do PC**: no `.env` dele, acrescente `EMISSAO_DATABASE_URL=` com a mesma string e
   reinicie o robô. Até isso, o painel (já no banco novo) não enxerga o robô e manda os
   pedidos para o GitHub (mais devagar, mas funciona).

## Conferir

- Abra a tela de Emissão de CT-e: os "Pedidos recentes" devem aparecer (vieram da cópia).
- Crie um pedido de teste e veja o robô pegar.
- No Neon, o projeto `emissao` passa a mostrar o consumo; o do banco separado deve cair.

## Voltar atrás

Apague `EMISSAO_DATABASE_URL` (Vercel, GitHub e `.env` do robô). As tabelas antigas continuam
no banco separado, só sem os pedidos criados depois da troca.

## Para quem mexe no código

- `app/db.py`: `engine_emissao` e `engine_da_tabela()` decidem o banco de cada tabela.
- Teste: `python tests/test_roteamento_emissao.py` (3 arquivos SQLite, 4 cenários e a cópia).
