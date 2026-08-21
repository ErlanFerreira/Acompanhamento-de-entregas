"""Executado pelo workflow do GitHub Actions (.github/workflows/sync.yml)
várias vezes ao dia. Autentica na API GW Serviços, busca as cargas e grava
no banco Postgres (Neon) -- o mesmo banco que o app lê.

Uso: python scripts/run_sync.py
Variáveis de ambiente necessárias: DATABASE_URL, GWSERVICOS_LOGIN,
GWSERVICOS_SENHA, GWSERVICOS_GUID (e opcionalmente SYNC_WINDOW_DAYS).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.sync import run_sync  # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        count = run_sync(db)
        print(f"Sincronização concluída: {count} cargas atualizadas.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
