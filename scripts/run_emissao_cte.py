"""Executado pelo workflow do GitHub Actions (.github/workflows/emissao_cte.yml)
a cada etapa de um pedido da tela "Emissão de CT-e" do painel -- só quando o
robô local do PC do escritório (scripts/robo_emissao.py) está desligado. As
etapas estão em app/emissao_pedidos.py.

Uso: python scripts/run_emissao_cte.py <emissao_id> <preparar|salvar>

Variáveis de ambiente necessárias: DATABASE_URL, PORTAL_EMAIL, PORTAL_SENHA
(no workflow, o login do GW próprio do bot). Precisa de `playwright install
chromium` antes de rodar.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal  # noqa: E402
from app.emissao_pedidos import processar  # noqa: E402
from app.models import EmissaoCte  # noqa: E402

logging.basicConfig(level=logging.INFO)


def main(emissao_id: int, acao: str):
    db = SessionLocal()
    try:
        processar(db, emissao_id, acao)
        em = db.query(EmissaoCte).filter(EmissaoCte.id == emissao_id).first()
        if em and em.status == "erro":
            # Comando de workflow do GitHub: vira uma "annotation" do run.
            resumo = " ".join((em.erro_mensagem or "").split())[:900]
            print(f"::error title=Emissão de CT-e {emissao_id} falhou::{resumo}")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2] not in ("preparar", "salvar"):
        print("Uso: python scripts/run_emissao_cte.py <emissao_id> <preparar|salvar>")
        sys.exit(1)
    main(int(sys.argv[1]), sys.argv[2])
