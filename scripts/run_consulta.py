"""Executado pelo workflow do GitHub Actions (.github/workflows/consulta.yml)
quando o usuário sobe uma planilha de consulta de notas fiscais no painel.

Uso: python scripts/run_consulta.py <job_id>
Variáveis de ambiente necessárias: DATABASE_URL, PORTAL_EMAIL, PORTAL_SENHA.
Precisa de `playwright install chromium` antes de rodar.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal  # noqa: E402
from app.models import ConsultaItem, ConsultaJob  # noqa: E402
from app.scrape import consultar_notas_fiscais  # noqa: E402


def main(job_id: int):
    db = SessionLocal()
    try:
        job = db.query(ConsultaJob).filter(ConsultaJob.id == job_id).first()
        if not job:
            print(f"Job {job_id} não encontrado.")
            return

        job.status = "processando"
        db.commit()

        itens = db.query(ConsultaItem).filter(ConsultaItem.job_id == job_id).all()
        numeros_unicos = sorted({item.nf_numero for item in itens if item.nf_numero})

        def progresso(i, total):
            job.processados = i
            db.commit()
            print(f"{i}/{total} NFs consultadas")

        resultados = consultar_notas_fiscais(numeros_unicos, progresso=progresso)

        for item in itens:
            matches = resultados.get(item.nf_numero, [])
            if not matches:
                item.encontrado = False
                continue

            primeiro, *extras = matches
            _preencher(item, primeiro)

            for extra in extras:
                novo = ConsultaItem(
                    job_id=job_id,
                    linha_idx=item.linha_idx,
                    linha_original=item.linha_original,
                    nf_numero=item.nf_numero,
                )
                _preencher(novo, extra)
                db.add(novo)

        job.status = "concluido"
        job.processados = job.total_itens
        db.commit()
        print(f"Job {job_id} concluído.")
    except Exception as exc:
        db.rollback()
        job = db.query(ConsultaJob).filter(ConsultaJob.id == job_id).first()
        if job:
            job.status = "erro"
            job.erro_mensagem = str(exc)[:490]
            db.commit()
        raise
    finally:
        db.close()


def _preencher(item: ConsultaItem, resultado: dict):
    item.encontrado = True
    item.cte = resultado.get("cte")
    item.status_entrega = resultado.get("status")
    item.previsao_entrega = resultado.get("previsao_entrega")
    item.data_entrega = resultado.get("data_entrega")
    item.dias_atraso = resultado.get("dias_atraso")
    item.observacao = resultado.get("observacao")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/run_consulta.py <job_id>")
        sys.exit(1)
    main(int(sys.argv[1]))
