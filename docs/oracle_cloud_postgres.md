# Banco principal na Oracle Cloud (Always Free)

Tira o banco principal da Neon (limite de 5 GB/mês de transferência) e coloca num
PostgreSQL próprio, numa VM gratuita da Oracle (cerca de 10 TB/mês de saída).
O app não muda: ele só usa a variável `DATABASE_URL`.

Os bancos separados (consultas, GDC, agendamento) continuam na Neon, cada um com a
sua cota. Se algum deles chegar perto do limite, o mesmo roteiro serve.

## 1. Conta e VM na Oracle

1. Criar a conta em oracle.com/cloud/free. Pede cartão só para confirmar identidade
   (o plano Always Free não cobra). **A região-base não muda depois**: escolha
   *Brazil East (São Paulo)* ou *Brazil Southeast (Vinhedo)*, perto da função da Vercel (`gru1`).
2. Compute > Instances > Create instance:
   - Imagem: **Ubuntu 22.04** (ou 24.04).
   - Forma: se houver, *Ampere A1* (1 OCPU e 6 GB de RAM). Se der "Out of host capacity",
     use *VM.Standard.E2.1.Micro* (1 GB de RAM: funciona para este banco, o script cria swap).
   - Rede: *Assign a public IPv4 address*.
   - Chave SSH: *Generate a key pair* e baixe a chave privada (sem ela não entra na VM).
3. Depois de criada, anote o **IP público**. Em Networking > Reserved public IPs, reserve
   esse IP para ele não mudar se a VM reiniciar.

## 2. Liberar a porta 5432 na rede da Oracle

Networking > Virtual Cloud Networks > (sua VCN) > Security Lists > Default > *Add Ingress Rules*:

| Source CIDR | Protocolo | Porta de destino |
|---|---|---|
| `0.0.0.0/0` | TCP | `5432` |

Não dá para restringir por IP: a Vercel e o GitHub Actions usam IPs que mudam. A proteção é
senha longa (gerada pelo script) + conexão só com SSL, sem usuário `postgres` exposto.
Mantenha a porta 22 (SSH) restrita ao seu IP, se puder.

## 3. Instalar o Postgres

No seu computador:

```bash
ssh -i caminho/da/chave.key ubuntu@IP_PUBLICO
```

Na VM:

```bash
git clone https://github.com/ErlanFerreira/Acompanhamento-de-entregas.git
sudo bash Acompanhamento-de-entregas/scripts/oracle/instalar_postgres.sh
```

No fim ele mostra a **DATABASE_URL** (`postgresql://painel:SENHA@IP:5432/painel?sslmode=require`).
Guarde num gerenciador de senhas: a senha não aparece de novo.

Teste de fora (no seu computador, com a URL no lugar):

```bash
python -c "import psycopg2; c=psycopg2.connect('COLE_A_URL_AQUI'); print(c.cursor().execute('select 1') or 'conectou')"
```

## 4. Copiar os dados e trocar

**Antes**: em Neon > Overview do projeto principal, veja o tamanho do banco (Storage) e o
percentual de transferência. A cópia lê o banco inteiro e conta como transferência da Neon:
se o banco tiver, por exemplo, 150 MB, a cópia gasta 150 MB dos que restam no mês.

1. GitHub > Settings > Secrets and variables > Actions:
   - `ANTIGO_DATABASE_URL`: troque pelo valor **atual** de `DATABASE_URL` (a Neon principal).
   - `DATABASE_URL`: troque pela URL da Oracle.
2. Vercel > Project > Settings > Environment Variables: `DATABASE_URL` = URL da Oracle.
   Depois, Deployments > Redeploy.
3. GitHub > Actions > **Migrar do banco antigo** > Run workflow com *simular* marcado
   (só conta, não grava). Conferindo os números, rode de novo sem simular. Pode repetir sem duplicar.
4. Abra o painel, entre, confira Painel, Comprovantes e Histórico. Rode **Sincronização** à mão
   uma vez para ver se grava.

O app e a sincronização passam a usar a Oracle assim que o passo 2 termina; o passo 3 traz o
histórico. Mantenha o projeto da Neon por 1 ou 2 semanas como reserva e depois apague.

## 5. Manutenção

- **Backup**: o script agenda um `pg_dump` diário às 03:00 em `/var/backups/painel` (7 dias).
  Isso protege de erro de dados, **não** de perder a VM. Copie de vez em quando para fora:
  `scp -i chave ubuntu@IP:/var/backups/painel/painel_AAAA-MM-DD.dump .` (precisa de `sudo` na VM
  para ler: `sudo cp` para a sua pasta antes).
- **Atualizações de segurança**: ficam automáticas (`unattended-upgrades`).
- **Instância ociosa**: a Oracle pode recolher VMs gratuitas com uso muito baixo por vários dias.
  Para evitar, converta a conta para *Pay As You Go*: não cobra enquanto você usar só recursos
  Always Free, e a instância deixa de ser recolhida.
- **Voltar atrás**: basta apontar `DATABASE_URL` (Vercel e GitHub) de volta para a Neon.
