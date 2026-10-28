"""Robô de emissão de CT-e instalado no PC do escritório.

Fica rodando, entra no GW uma vez (com o login próprio do robô) e vai
emitindo os pedidos da tela "Emissão de CT-e" do painel assim que chegam --
sem os 1-2 min que o GitHub Actions leva pra começar cada etapa, e sem
entrar no GW de novo a cada CT-e. Enquanto ele dá sinal de vida, o painel
manda os pedidos pra cá; desligado, o painel volta a usar o GitHub Actions.

Uso (ou dois cliques em scripts/robo_emissao.bat):
    python scripts/robo_emissao.py [--ver] [--simular]

    --ver       mostra o navegador trabalhando
    --simular   faz tudo menos salvar no GW (só aceito com banco local de teste)

No .env deste PC:
    CONSULTAS_DATABASE_URL  banco separado do painel (onde ficam os pedidos)
    EMISSAO_PORTAL_EMAIL    login do GW do robô (o mesmo dos Secrets
    EMISSAO_PORTAL_SENHA    GW_BOT_EMAIL/GW_BOT_SENHA do GitHub)
    EMISSAO_PORTAL_ORGANIZACAO  (opcional) CNPJ, se o robô tiver mais de uma organização

Só um robô por vez precisa ficar ligado; se houver dois, cada pedido é
pego por um só.
"""

import argparse
import datetime
import logging
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402

# Login do GW do robô -- não o PORTAL_EMAIL de quem usa o PC (o GW só aceita
# uma sessão por login, então usar o mesmo derrubaria a pessoa).
if os.environ.get("EMISSAO_PORTAL_EMAIL"):
    config.PORTAL_EMAIL = os.environ["EMISSAO_PORTAL_EMAIL"]
    config.PORTAL_SENHA = os.environ.get("EMISSAO_PORTAL_SENHA", "")
if os.environ.get("EMISSAO_PORTAL_ORGANIZACAO"):
    config.PORTAL_ORGANIZACAO = os.environ["EMISSAO_PORTAL_ORGANIZACAO"]

from app.db import SessionLocal, criar_tabelas_separadas, engine, engine_separado  # noqa: E402
from app.emissao_cte import SessaoGW  # noqa: E402
from app.emissao_pedidos import ETAPA_DO_STATUS, processar  # noqa: E402
from app.models import EmissaoCte, EmissaoRobo  # noqa: E402
from app.seed import _adicionar_colunas_faltantes, init_db_and_seed  # noqa: E402

NOME = socket.gethostname()[:120]
# Intervalos pensados pro tráfego do banco (a cota mensal de rede da Neon já
# estourou uma vez): procurar pedido a cada 5 s, sinal de vida a cada 30 s.
INTERVALO_BUSCA = 5
INTERVALO_SINAL = 30

logger = logging.getLogger("robo_emissao")


def _sinal(db, situacao: str, ligado: bool = True) -> None:
    robo = db.query(EmissaoRobo).filter(EmissaoRobo.nome == NOME).first()
    if not robo:
        robo = EmissaoRobo(nome=NOME)
        db.add(robo)
    robo.visto_em = datetime.datetime.utcnow() if ligado else None
    robo.situacao = situacao[:255]
    db.commit()


def _proximo(db):
    """O pedido mais antigo mandado pro robô local, já reservado pra este PC
    (num UPDATE só: se houver outro robô ligado, só um pega cada pedido)."""
    candidatos = (
        db.query(EmissaoCte.id)
        .filter(EmissaoCte.executor == "local", EmissaoCte.status.in_(tuple(ETAPA_DO_STATUS)))
        .order_by(EmissaoCte.atualizado_em, EmissaoCte.id)
        .limit(5)
        .all()
    )
    for (emissao_id,) in candidatos:
        pegou = (
            db.query(EmissaoCte)
            .filter(EmissaoCte.id == emissao_id, EmissaoCte.executor == "local")
            .update({"executor": f"local:{NOME}"}, synchronize_session=False)
        )
        db.commit()
        if pegou:
            return db.query(EmissaoCte).filter(EmissaoCte.id == emissao_id).first()
    return None


def _preparar_banco(simular: bool) -> None:
    if engine_separado is engine:
        if not engine.url.drivername.startswith("sqlite"):
            raise SystemExit("CONSULTAS_DATABASE_URL não está no .env: o robô não veria os pedidos do painel.")
        if not simular:
            raise SystemExit(
                "Sem CONSULTAS_DATABASE_URL no .env o robô usaria o banco local de teste -- "
                "configure o banco do painel (ou rode com --simular pra testar)."
            )
        init_db_and_seed()
        return
    if simular:
        raise SystemExit("--simular só roda com o banco local de teste (tire o CONSULTAS_DATABASE_URL do .env).")
    criar_tabelas_separadas()
    _adicionar_colunas_faltantes(so_separado=True)


def _trabalhar(sessao: SessaoGW, simular: bool) -> None:
    ultimo_sinal = 0.0
    while True:
        db = SessionLocal()
        try:
            if time.monotonic() - ultimo_sinal > INTERVALO_SINAL:
                _sinal(db, "aguardando pedidos")
                ultimo_sinal = time.monotonic()
            em = _proximo(db)
            if em is None:
                time.sleep(INTERVALO_BUSCA)
                continue
            etapa = ETAPA_DO_STATUS[em.status]
            _sinal(db, f"{etapa} pedido {em.id} (NF {int(em.chave_nfe[25:34])})")
            ultimo_sinal = time.monotonic()
            inicio = time.monotonic()
            logger.info("Pedido %s: %s %s NF %s (%s)", em.id, etapa, em.tipo, int(em.chave_nfe[25:34]), em.solicitante)
            processar(db, em.id, etapa, sessao=sessao, simular=simular)
            em = db.query(EmissaoCte).filter(EmissaoCte.id == em.id).first()
            logger.info("Pedido %s: %s em %.0f s%s", em.id, em.status, time.monotonic() - inicio,
                        f" -- {em.erro_mensagem}" if em.status == "erro" else "")
        finally:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Robô de emissão de CT-e (fica rodando neste PC).")
    parser.add_argument("--ver", action="store_true", help="mostra o navegador")
    parser.add_argument("--simular", action="store_true", help="não salva nada no GW (só com banco local de teste)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%d/%m %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler("robo_emissao.log", encoding="utf-8")],
    )
    _preparar_banco(args.simular)
    logger.info("Robô de emissão ligado neste PC (%s)%s. Deixe esta janela aberta; Ctrl+C desliga.",
                NOME, " -- SIMULAÇÃO, nada é salvo no GW" if args.simular else "")
    try:
        while True:
            try:
                with SessaoGW(headless=not args.ver) as sessao:
                    logger.info("Entrou no GW. Aguardando pedidos do painel...")
                    _trabalhar(sessao, args.simular)
            except KeyboardInterrupt:
                raise
            except Exception:
                # Navegador caiu, GW fora do ar, banco sem rede...: espera e
                # recomeça do zero (abre o navegador e entra de novo).
                logger.exception("O robô parou com erro; tentando de novo em 30 s.")
                time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Desligando...")
    finally:
        db = SessionLocal()
        try:
            _sinal(db, "desligado", ligado=False)  # o painel volta a usar o GitHub na hora
        except Exception:
            pass
        finally:
            db.close()


if __name__ == "__main__":
    main()
