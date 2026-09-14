"""Executado pelo workflow do GitHub Actions (.github/workflows/consulta.yml)
quando o usuário sobe uma planilha de consulta de notas fiscais no painel.

Uso: python scripts/run_consulta.py <job_id>
Variáveis de ambiente necessárias: DATABASE_URL, PORTAL_EMAIL, PORTAL_SENHA.
Precisa de `playwright install chromium` antes de rodar.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal  # noqa: E402
from app.models import ConsultaItem, ConsultaJob  # noqa: E402
from app.scrape import consultar_notas_fiscais, empresa_confere  # noqa: E402


def _linha_dict(item: ConsultaItem) -> dict:
    try:
        return json.loads(item.linha_original or "{}")
    except ValueError:
        return {}


def _chave_item(item: ConsultaItem, job: ConsultaJob) -> tuple:
    """(numero, serie) -- serie só entra se a planilha tiver essa coluna
    detectada e a linha tiver valor preenchido nela."""
    serie = None
    if job.coluna_serie:
        valor = _linha_dict(item).get(job.coluna_serie)
        if valor not in (None, ""):
            serie = str(valor).strip()
    return (item.nf_numero, serie)


def _nomes_esperados(item: ConsultaItem, job: ConsultaJob) -> list[str]:
    """Remetente/destinatário que a própria planilha diz pra essa linha --
    usado pra confirmar que o CT-e achado é mesmo desse cliente."""
    if not job.coluna_remetente and not job.coluna_destinatario:
        return []
    linha = _linha_dict(item)
    nomes = []
    for coluna in (job.coluna_remetente, job.coluna_destinatario):
        if coluna:
            valor = linha.get(coluna)
            if valor not in (None, ""):
                nomes.append(str(valor))
    return nomes


def _filtrar_por_parceiro(matches: list[dict], nomes_esperados: list[str]) -> list[dict]:
    """Descarta CT-e cujo remetente/destinatário não bate com o que a
    planilha informou -- o número da NF sozinho não é único, então sem
    essa checagem podíamos anexar informação de outro cliente."""
    if not nomes_esperados:
        return matches  # planilha não tem colunas pra validar -- mantém como antes
    validos = []
    for m in matches:
        rem, dest = m.get("remetente"), m.get("destinatario")
        if not rem and not dest:
            # não deu pra extrair nome desse CT-e (ex: tipo != "Normal") --
            # não dá pra invalidar por falta de dado, mantém.
            validos.append(m)
        elif empresa_confere(nomes_esperados, rem or "", dest or ""):
            validos.append(m)
    return validos


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

        consultas_unicas: dict[tuple, dict] = {}
        for item in itens:
            if not item.nf_numero:
                continue
            chave = _chave_item(item, job)
            consultas_unicas.setdefault(chave, {"numero": chave[0], "serie": chave[1]})

        def progresso(i, total):
            job.processados = i
            db.commit()
            print(f"{i}/{total} NFs consultadas")

        resultados = consultar_notas_fiscais(list(consultas_unicas.values()), progresso=progresso)

        for item in itens:
            if not item.nf_numero:
                item.encontrado = False
                continue

            chave = _chave_item(item, job)
            matches = resultados.get(chave, [])
            matches = _filtrar_por_parceiro(matches, _nomes_esperados(item, job))
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
