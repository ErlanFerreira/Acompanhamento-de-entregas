"""Envia o resumo diário de pendências por e-mail a cada analista que tem
e-mail cadastrado (tela Usuários). Executado pelo workflow do GitHub Actions
(.github/workflows/resumo_diario.yml) nos dias úteis de manhã.

Uso: python scripts/enviar_resumos.py [--login maria.silva] [--dry-run] [--saida pasta]
  --login    envia só para esse analista (teste)
  --dry-run  não envia: relata o que seria enviado e grava o HTML em --saida
Variáveis de ambiente: DATABASE_URL e as do envio (ver app/resumo_diario.py).
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal  # noqa: E402
from app.resumo_diario import executar  # noqa: E402
from app.seed import init_db_and_seed  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--saida", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    init_db_and_seed()  # garante a coluna usuarios.email e as tabelas do histórico
    db = SessionLocal()
    try:
        for linha in executar(db, login=args.login or None, dry_run=args.dry_run, saida=args.saida):
            print(linha)
    finally:
        db.close()


if __name__ == "__main__":
    main()
