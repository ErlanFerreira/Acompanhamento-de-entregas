"""Executado pelo workflow do GitHub Actions (.github/workflows/sync.yml)
várias vezes ao dia. Faz login no portal Webtrans, gera o relatório
personalizado "Pendências" e grava os dados no banco Postgres (Neon) -- o
mesmo banco que o app lê.

Uso: python scripts/run_sync.py
Variáveis de ambiente necessárias: DATABASE_URL, PORTAL_EMAIL, PORTAL_SENHA
(e opcionalmente SYNC_WINDOW_DAYS). Precisa de `playwright install chromium`
antes de rodar (ver requirements-sync.txt).
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
