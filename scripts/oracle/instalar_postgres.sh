#!/usr/bin/env bash
# Instala e configura o PostgreSQL numa VM Ubuntu (22.04 ou 24.04) da Oracle Cloud
# (Always Free), pronto pro painel acessar pela internet com SSL.
#
# Uso (na VM, por SSH):
#     sudo bash instalar_postgres.sh
#
# Variáveis opcionais: DB_NOME (painel), DB_USUARIO (painel), DB_SENHA (gerada).
# Pode rodar de novo: não recria o que já existe e não troca a senha do usuário
# (a menos que DB_SENHA seja informada).
#
# No fim mostra a URL de conexão (DATABASE_URL) -- guarde, a senha só aparece aqui.
# Roteiro completo: docs/oracle_cloud_postgres.md
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode com sudo: sudo bash $0" >&2
  exit 1
fi

DB_NOME="${DB_NOME:-painel}"
DB_USUARIO="${DB_USUARIO:-painel}"
SENHA_INFORMADA="${DB_SENHA:-}"
DB_SENHA="${DB_SENHA:-$(openssl rand -hex 16)}"  # só letras e números: entra na URL sem escape

export DEBIAN_FRONTEND=noninteractive

echo "==> Instalando pacotes"
apt-get update -y
apt-get install -y postgresql postgresql-contrib openssl iptables iptables-persistent netfilter-persistent unattended-upgrades curl

# VM pequena (1 GB de RAM): sem swap o Postgres pode ser morto por falta de memória.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 2048 ] && [ -z "$(swapon --show --noheadings)" ]; then
  echo "==> Criando swap de 1 GB"
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

PGVER=$(ls /etc/postgresql | sort -V | tail -1)
CONF_DIR="/etc/postgresql/${PGVER}/main"

echo "==> Certificado SSL (autoassinado, 10 anos)"
if [ ! -f "${CONF_DIR}/painel.crt" ]; then
  openssl req -new -x509 -days 3650 -nodes -subj "/CN=painel-db" \
    -keyout "${CONF_DIR}/painel.key" -out "${CONF_DIR}/painel.crt"
  chown postgres:postgres "${CONF_DIR}/painel.key" "${CONF_DIR}/painel.crt"
  chmod 600 "${CONF_DIR}/painel.key"
fi

echo "==> Configurando o PostgreSQL ${PGVER}"
if [ "$MEM_MB" -ge 4096 ]; then SHARED="512MB"; else SHARED="128MB"; fi
cat > "${CONF_DIR}/conf.d/painel.conf" <<EOF
listen_addresses = '*'
ssl = on
ssl_cert_file = '${CONF_DIR}/painel.crt'
ssl_key_file = '${CONF_DIR}/painel.key'
password_encryption = scram-sha-256
max_connections = 60
shared_buffers = ${SHARED}
work_mem = 4MB
log_min_duration_statement = 2000
EOF

# Só aceita conexão remota COM SSL, deste usuário, neste banco.
if ! grep -q "painel-remoto" "${CONF_DIR}/pg_hba.conf"; then
  cat >> "${CONF_DIR}/pg_hba.conf" <<EOF

# painel-remoto: Vercel e GitHub Actions (IPs variáveis), sempre com SSL e senha
hostssl ${DB_NOME} ${DB_USUARIO} 0.0.0.0/0 scram-sha-256
hostssl ${DB_NOME} ${DB_USUARIO} ::/0      scram-sha-256
EOF
fi

systemctl enable postgresql
systemctl restart postgresql

echo "==> Criando usuário e banco"
EXISTE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USUARIO}'")
if [ "$EXISTE" != "1" ]; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE ${DB_USUARIO} LOGIN PASSWORD '${DB_SENHA}'"
  SENHA_MOSTRAR="$DB_SENHA"
elif [ -n "$SENHA_INFORMADA" ]; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER ROLE ${DB_USUARIO} PASSWORD '${DB_SENHA}'"
  SENHA_MOSTRAR="$DB_SENHA"
else
  SENHA_MOSTRAR="<a senha já definida antes foi mantida>"
fi
if [ "$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NOME}'")" != "1" ]; then
  sudo -u postgres createdb -O "${DB_USUARIO}" "${DB_NOME}"
fi

echo "==> Liberando a porta 5432 no firewall da VM"
# As imagens da Oracle trazem iptables com REJECT no fim: a regra tem que vir antes.
iptables -C INPUT -p tcp --dport 5432 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 5432 -j ACCEPT
ip6tables -C INPUT -p tcp --dport 5432 -j ACCEPT 2>/dev/null || ip6tables -I INPUT 1 -p tcp --dport 5432 -j ACCEPT
netfilter-persistent save

echo "==> Backup diário (03:00), guarda 7 dias em /var/backups/painel"
install -d -o postgres -g postgres -m 700 /var/backups/painel
cat > /usr/local/bin/painel-backup.sh <<EOF
#!/usr/bin/env bash
set -euo pipefail
pg_dump -Fc ${DB_NOME} > /var/backups/painel/painel_\$(date +%F).dump
find /var/backups/painel -name 'painel_*.dump' -mtime +7 -delete
EOF
chmod 755 /usr/local/bin/painel-backup.sh
echo "0 3 * * * postgres /usr/local/bin/painel-backup.sh" > /etc/cron.d/painel-backup
chmod 644 /etc/cron.d/painel-backup

echo "==> Atualizações de segurança automáticas"
dpkg-reconfigure -f noninteractive unattended-upgrades || true

IP=$(curl -s --max-time 5 https://ifconfig.me || true)
IP="${IP:-<IP_PUBLICO_DA_VM>}"

echo
echo "================ PRONTO ================"
echo "DATABASE_URL (guarde, a senha não aparece de novo):"
echo "postgresql://${DB_USUARIO}:${SENHA_MOSTRAR}@${IP}:5432/${DB_NOME}?sslmode=require"
echo
echo "Falta liberar a porta 5432/TCP na rede da Oracle (Security List) -- ver docs/oracle_cloud_postgres.md"
