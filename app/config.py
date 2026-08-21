import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite:///./local.db"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

GWSERVICOS_LOGIN = os.environ.get("GWSERVICOS_LOGIN", "")
GWSERVICOS_SENHA = os.environ.get("GWSERVICOS_SENHA", "")
GWSERVICOS_GUID = os.environ.get("GWSERVICOS_GUID", "")

SYNC_WINDOW_DAYS = int(os.environ.get("SYNC_WINDOW_DAYS", "90"))

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_NOME = os.environ.get("ADMIN_NOME", "Administrador")
