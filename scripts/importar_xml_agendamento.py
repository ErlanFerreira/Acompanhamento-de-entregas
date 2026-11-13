"""Importa sozinho os XML de NF-e de uma pasta para a tela "Agendamento de
Entregas" do painel.

Fica rodando num PC que enxerga a pasta (local ou de rede): a cada minuto
procura XML novos ou alterados (inclusive em subpastas), lê cada um e importa
as NFs dos clientes que agendam entrega (hoje a Fotus -- ver CLIENTES em
app/agendamento.py). O telefone sai da observação da nota e a previsão do
CT-e sincronizado, igual ao botão "Importar XML" da tela. Os outros XML
(outros clientes, CT-e, eventos) são ignorados.

Só LÊ a pasta: não move, não renomeia, não apaga nada. Os arquivos já vistos
ficam anotados em agendamento_xml_vistos.json (na raiz do projeto) pra não
serem lidos de novo; um arquivo alterado é lido outra vez.

Uso (ou dois cliques em scripts/importar_xml_agendamento.bat):
    python scripts/importar_xml_agendamento.py [--pasta CAMINHO] [--uma-vez] [--dias N]

    --pasta     pasta dos XML (padrão: AGENDAMENTO_PASTA_XML do .env)
    --uma-vez   varre uma vez e sai (pra rodar pelo Agendador de Tarefas)
    --dias      só arquivos alterados nos últimos N dias (padrão 30) --
                evita importar o histórico inteiro na primeira vez
    --intervalo segundos entre as varreduras (padrão 60)

No .env deste PC:
    AGENDAMENTO_PASTA_XML   pasta dos XML, ex.: \\\\servidor\\xml\\fotus
    DATABASE_URL            banco principal (CT-e e previsão sincronizados)
    CONSULTAS_DATABASE_URL  banco separado (onde ficam os agendamentos)
Os dois bancos são os mesmos da Vercel.
"""

import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import agendamento, config  # noqa: E402
from app.db import SessionLocal  # noqa: E402

QUEM = "Importação automática (pasta)"
ARQUIVO_VISTOS = RAIZ / "agendamento_xml_vistos.json"
MAX_BYTES = 2_000_000  # XML de NF-e tem poucos KB; maior que isso não é nota

logger = logging.getLogger("importar_xml_agendamento")


def _carregar_vistos() -> dict:
    try:
        return json.loads(ARQUIVO_VISTOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _salvar_vistos(vistos: dict) -> None:
    temp = ARQUIVO_VISTOS.with_suffix(".tmp")
    temp.write_text(json.dumps(vistos, ensure_ascii=False, indent=0), encoding="utf-8")
    temp.replace(ARQUIVO_VISTOS)


def _assinatura(caminho: Path) -> str:
    st = caminho.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def _ler(caminho: Path) -> str:
    bruto = caminho.read_bytes()
    for codificacao in ("utf-8-sig", "latin-1"):
        try:
            return bruto.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return bruto.decode("utf-8", errors="replace")


def varrer(pasta: Path, dias: int, vistos: dict) -> dict:
    """Uma passada pela pasta. Devolve um resumo do que fez."""
    resumo = {"lidos": 0, "importados": 0, "novos": 0, "sem_telefone": [], "ignorados": 0, "erros": 0}
    limite = time.time() - dias * 86400
    candidatos = []
    for caminho in pasta.rglob("*"):
        if caminho.suffix.lower() != ".xml" or not caminho.is_file():
            continue
        try:
            assinatura = _assinatura(caminho)
            if caminho.stat().st_mtime < limite:
                continue
        except OSError:
            continue  # sumiu no meio da varredura
        chave = str(caminho)
        if vistos.get(chave) != assinatura:
            candidatos.append((caminho, chave, assinatura))
    if not candidatos:
        return resumo

    agendamento.preparar_tabelas()
    db_ag, db = agendamento.SessionAg(), SessionLocal()
    try:
        for caminho, chave, assinatura in candidatos:
            resumo["lidos"] += 1
            try:
                if caminho.stat().st_size > MAX_BYTES:
                    raise ValueError("arquivo grande demais para ser NF-e")
                dados = agendamento.ler_xml_nfe(_ler(caminho))
            except (ET.ParseError, ValueError) as e:
                # CT-e, evento, XML quebrado... não é uma NF-e: não tenta de novo.
                logger.debug("Ignorado %s (%s)", caminho.name, e)
                resumo["ignorados"] += 1
                vistos[chave] = assinatura
                continue
            except OSError as e:
                # Arquivo ainda sendo copiado / sem acesso: tenta na próxima.
                logger.warning("Não deu pra ler %s agora (%s) -- tento de novo depois.", caminho, e)
                resumo["erros"] += 1
                continue
            if not dados["nf"] or not agendamento.eh_cliente_agendado(dados["emitente"]):
                resumo["ignorados"] += 1
                vistos[chave] = assinatura
                continue
            try:
                a, novo = agendamento.gravar(db_ag, db, QUEM, dados)
                db_ag.commit()
            except Exception:
                db_ag.rollback()
                logger.exception("Falha ao gravar a NF %s (%s) -- tento de novo depois.", dados["nf"], caminho.name)
                resumo["erros"] += 1
                continue
            vistos[chave] = assinatura
            resumo["importados"] += 1
            resumo["novos"] += int(novo)
            if not a.telefone:
                resumo["sem_telefone"].append(a.nf)
            logger.info("NF %s %s -- %s, telefone %s, %s", a.nf, "importada" if novo else "atualizada",
                        a.cliente or "?", a.telefone or "NÃO ENCONTRADO",
                        f"CT-e {a.cte}" if a.cte else "CT-e ainda não sincronizado")
    finally:
        db_ag.close()
        db.close()
        _salvar_vistos(vistos)
    return resumo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pasta", default=os.environ.get("AGENDAMENTO_PASTA_XML", ""))
    parser.add_argument("--uma-vez", action="store_true")
    parser.add_argument("--dias", type=int, default=30)
    parser.add_argument("--intervalo", type=int, default=60)
    parser.add_argument("--banco-local", action="store_true", help="aceita rodar sem os bancos da Vercel (teste)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(RAIZ / "agendamento_xml.log", encoding="utf-8")],
    )
    if not args.pasta:
        logger.error("Informe a pasta: AGENDAMENTO_PASTA_XML no .env ou --pasta CAMINHO.")
        return 2
    pasta = Path(args.pasta)
    if not pasta.is_dir():
        logger.error("Pasta não encontrada (ou sem acesso): %s", pasta)
        return 2
    if not args.banco_local and (config.DATABASE_URL.startswith("sqlite") or not os.environ.get("CONSULTAS_DATABASE_URL")):
        logger.error("Faltam DATABASE_URL e/ou CONSULTAS_DATABASE_URL no .env (os mesmos da Vercel) -- "
                     "sem eles os agendamentos não chegam no painel.")
        return 2

    logger.info("Vigiando %s (XML dos últimos %d dias, clientes: %s)", pasta, args.dias, ", ".join(agendamento.CLIENTES))
    vistos = _carregar_vistos()
    while True:
        try:
            r = varrer(pasta, args.dias, vistos)
            if r["lidos"]:
                logger.info("Varredura: %d arquivo(s) lido(s), %d NF(s) importada(s) (%d nova(s)), %d ignorado(s), %d erro(s).",
                            r["lidos"], r["importados"], r["novos"], r["ignorados"], r["erros"])
                if r["sem_telefone"]:
                    logger.warning("Sem telefone na observação (completar na tela): NF %s", ", ".join(r["sem_telefone"]))
        except Exception:
            # Banco fora do ar, pasta de rede caiu... segue tentando.
            logger.exception("Falha na varredura -- tento de novo em %d s.", args.intervalo)
        if args.uma_vez:
            return 0
        time.sleep(args.intervalo)


if __name__ == "__main__":
    sys.exit(main())
