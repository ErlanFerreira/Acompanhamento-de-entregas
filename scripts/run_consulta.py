"""Executado pelo workflow do GitHub Actions (.github/workflows/consulta.yml)
quando o usuário sobe uma planilha de consulta de notas fiscais no painel.

Uso: python scripts/run_consulta.py <job_id>
Variáveis de ambiente necessárias: DATABASE_URL, PORTAL_EMAIL, PORTAL_SENHA.
Precisa de `playwright install chromium` antes de rodar.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, engine, engine_consultas  # noqa: E402
from app.models import ConsultaItem, ConsultaJob  # noqa: E402
from app.scrape import TEXTOS_SEM_RESULTADO, consultar_notas_fiscais, nomes_conferem  # noqa: E402


def _nomes_esperados(item: ConsultaItem) -> list[str]:
    """Remetente/destinatário/tomador/cliente que a própria planilha diz
    pra essa linha (resolvido no upload -- ver api_consultas_create) --
    usado pra confirmar que o CT-e achado é mesmo desse cliente."""
    try:
        return json.loads(item.nomes_esperados or "[]")
    except ValueError:
        return []


# Quantos nomes da planilha precisam bater com partes do CT-e pra confirmar que
# é a nota procurada (o número da NF sozinho não basta: não é único).
MINIMO_CONFIRMACOES = 2


def _nomes_distintos(nomes: list[str]) -> list[str]:
    """Tira repetidos (a mesma empresa em duas colunas da planilha, ou só com
    LTDA/S/A de diferença) -- senão um nome só contaria como dois."""
    saida: list[str] = []
    for n in nomes:
        if n and not any(nomes_conferem(n, o) for o in saida):
            saida.append(n)
    return saida


def _exigido(nomes_distintos: list[str]) -> int:
    # Planilha com só um nome (ex: uma coluna "Cliente") não tem como dar dois.
    return min(MINIMO_CONFIRMACOES, len(nomes_distintos))


def _pontos(m: dict, nomes_distintos: list[str]) -> int:
    """Quantos nomes da planilha batem com alguma parte do CT-e
    (consignatário/tomador, remetente ou destinatário)."""
    partes = [m.get("consignatario"), m.get("remetente"), m.get("destinatario")]
    return sum(1 for n in nomes_distintos if any(p and nomes_conferem(n, p) for p in partes))


def _filtrar_por_parceiro(matches: list[dict], nomes_esperados: list[str]) -> list[dict]:
    """Confirma que o CT-e achado pela NF é do cliente da planilha -- o número
    da NF sozinho não é único, então sem essa checagem podíamos anexar
    informação de outro cliente.

    Pelo menos DOIS nomes diferentes da planilha (consignatário, tomador,
    remetente, destinatário ou cliente) precisam bater com partes do CT-e
    (consignatário/tomador, remetente, destinatário) -- ou todos, se a
    planilha só tem um nome. Com vários CT-e para a mesma NF, ficam só os que
    batem mais. Nomes são comparados de forma tolerante (LTDA, S/A, acento,
    nome parcial)."""
    if not nomes_esperados:
        return matches  # planilha não tem colunas pra validar -- mantém como antes
    nomes = _nomes_distintos(nomes_esperados)
    exigido = _exigido(nomes)
    sem_nome = [m for m in matches if not (m.get("consignatario") or m.get("remetente") or m.get("destinatario"))]
    pontuados = [(m, _pontos(m, nomes)) for m in matches if m not in sem_nome]
    melhor = max([p for _, p in pontuados] or [0])
    validos = [m for m, p in pontuados if p >= max(exigido, 1) and p == melhor]
    # CT-e sem nome extraível (ex: tipo != "Normal") não dá pra invalidar: mantém.
    return validos + sem_nome


def main(job_id: int):
    # Anotação do run no GitHub: confirma que o robô usa o banco separado das
    # consultas (o secret CONSULTAS_DATABASE_URL) e não o principal.
    if engine_consultas is engine:
        print("::warning title=Banco das consultas::CONSULTAS_DATABASE_URL não chegou ao robô -- usando o banco principal. "
              "Confira se é um Secret de repositório (Settings > Secrets and variables > Actions > Secrets).")
    else:
        print("::notice title=Banco das consultas::usando o banco separado das consultas.")
    db = SessionLocal()
    try:
        job = db.query(ConsultaJob).filter(ConsultaJob.id == job_id).first()
        if not job:
            print(f"Job {job_id} não encontrado.")
            return

        job.status = "processando"
        job.ultimo_sinal = datetime.datetime.utcnow()
        db.commit()

        itens = db.query(ConsultaItem).filter(ConsultaItem.job_id == job_id).all()
        numeros_unicos = sorted({item.nf_numero for item in itens if item.nf_numero})

        def progresso(i, total):
            job.processados = i
            job.ultimo_sinal = datetime.datetime.utcnow()
            db.commit()
            print(f"{i}/{total} NFs consultadas")

        resultados = consultar_notas_fiscais(numeros_unicos, progresso=progresso)

        for item in itens:
            if not item.nf_numero:
                item.encontrado = False
                continue

            matches = resultados.get(item.nf_numero, [])
            if matches is None:
                # a consulta dessa NF falhou no portal (diferente de "não encontrada")
                item.encontrado = False
                item.erro = "Falha ao consultar no portal"
                continue
            nomes = _nomes_esperados(item)
            todos = matches
            matches = _filtrar_por_parceiro(todos, nomes)
            if not matches:
                item.encontrado = False
                # Diz POR QUE deu "Não" (vai pra coluna Observação da planilha):
                # o portal não achou a NF, ou achou mas é de outro cliente.
                if todos:
                    t = todos[0]
                    distintos = _nomes_distintos(nomes)
                    item.observacao = (
                        f"NF localizada em {len(todos)} CT-e, mas só {_pontos(t, distintos)} de {len(distintos)} nome(s) "
                        f"da planilha batem (mínimo {_exigido(distintos)}) "
                        f"(ex.: CT-e {t.get('cte')} -- consignatário: {t.get('consignatario') or '?'} / "
                        f"remetente: {t.get('remetente') or '?'} / destinatário: {t.get('destinatario') or '?'}). "
                        f"Cliente(s) na planilha: {', '.join(nomes)}"
                    )[:900]
                else:
                    texto = TEXTOS_SEM_RESULTADO.get(item.nf_numero)
                    item.observacao = "NF não localizada na Consulta Entrega do GW" + (f" | texto do portal: {texto}" if texto else "")
                continue

            primeiro, *extras = matches
            _preencher(item, primeiro)

            for extra in extras:
                novo = ConsultaItem(
                    job_id=job_id,
                    aba=item.aba,
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
        # Comando de workflow do GitHub: vira uma "annotation" do run, legível
        # sem abrir o log (e pela API pública), com o motivo da falha.
        resumo = " ".join(str(exc).split())[:900]
        print(f"::error title=Consulta {job_id} falhou::{resumo}")
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
