import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite:///./local.db"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Credenciais do PORTAL Webtrans (não da API GW Serviços -- essa API só
# enxerga cargas onde o CNPJ logado é remetente/destinatário, então a
# sincronização usa automação de navegador com o login normal do usuário,
# que tem visão completa da empresa via o relatório personalizado "Pendências").
PORTAL_EMAIL = os.environ.get("PORTAL_EMAIL", "")
PORTAL_SENHA = os.environ.get("PORTAL_SENHA", "")

SYNC_WINDOW_DAYS = int(os.environ.get("SYNC_WINDOW_DAYS", "90"))

# Senha única compartilhada para acessar o painel (sem contas de usuário).
PAINEL_SENHA = os.environ.get("PAINEL_SENHA", "")

# Para o botão "Atualizar agora" do painel disparar a sincronização via
# GitHub Actions (a Vercel não consegue rodar o navegador automatizado).
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # ex.: "ErlanFerreira/Acompanhamento-de-entregas"
GITHUB_DISPATCH_TOKEN = os.environ.get("GITHUB_DISPATCH_TOKEN", "")
