"""Painel pronto: a lista de CT-e do Painel/Comprovantes, montada uma vez por
sincronização e guardada compactada no banco.

Por quê: o plano gratuito da Neon limita a transferência de saída do banco
(5 GB/mês). Servir o Painel lendo as linhas de `cargas` a cada abertura (e a
cada sincronização nova, para cada escopo de analista e cada instância da
Vercel) consumia ~0,28 GB por dia. Aqui o robô da sincronização, que já tem
todos os CT-e na memória, monta a lista inteira de cada período (3, 7, 14 e 30
dias), compacta e grava -- gravar não conta na cota. O app lê só esse arquivo
(~1/8 do tamanho) e aplica o escopo do analista em Python.

Se não houver Painel pronto que sirva (ex.: ainda não rodou a primeira
sincronização, período "todas as datas", busca), o app usa o caminho antigo, as
linhas de `cargas`. O resultado é o mesmo; só muda de onde os dados saem.

Também mede: `meta` ganha "uso:AAAA-MM-DD:<rótulo>" = "chamadas|bytes" com o que
cada leitura do Painel puxou do banco -- para descobrir, com número, de onde
vem o consumo (select chave, valor from meta where chave like 'uso:%').
"""

import datetime
import gzip
import json
import logging
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Session

from .db import Base
from .logistica import classificar as classificar_logistica
from .models import Meta
from .prazo_comprovante import prazo_comprovante

logger = logging.getLogger("painel_snapshot")

# Períodos que as telas pedem (3, 7, 14 e 30 dias; ver dashboard.js).
PERIODOS = (3, 7, 14, 30)
# A tela pede "hoje - período - 2 dias"; o Painel pronto cobre 3 dias a mais que
# o período para servir também no dia seguinte, antes da próxima sincronização.
FOLGA_DIAS = 3


class PainelSnapshot(Base):
    """Um período do Painel, já montado e compactado (gzip de uma lista JSON)."""

    __tablename__ = "painel_snapshots"

    periodo = Column(Integer, primary_key=True)
    validade = Column(String(40), nullable=False)      # = meta.last_sync_at da sincronização que gerou
    cobre_desde = Column(String(10), nullable=False)   # menor data de emissão que está nele (AAAA-MM-DD)
    registros = Column(Integer, nullable=False, default=0)
    corpo = Column(LargeBinary, nullable=False)
    gerado_em = Column(DateTime, default=datetime.datetime.utcnow)


def registro_para_dict(r) -> dict:
    """CT-e como o Painel recebe. `r` pode ser um `Carga` ou qualquer objeto com os
    mesmos atributos (o robô da sincronização usa os registros do relatório)."""
    prazo_dias, distancia_km = prazo_comprovante(r.cidade_dest, r.uf_dest)
    return {
        "id_cte": r.id_cte,
        "emissao": r.emissao,
        "cte": r.cte,
        "remetente": r.remetente,
        "consignatario": r.consignatario,
        "cidade_cons": r.cidade_cons,
        "destinatario": r.destinatario,
        "cidade_dest": r.cidade_dest,
        "uf_dest": r.uf_dest,
        "manifesto": r.manifesto,
        "romaneio": r.romaneio,
        "cidade_manifesto": r.cidade_manifesto,
        "cidade_romaneio": r.cidade_romaneio,
        "logistica": classificar_logistica(r),  # "rota" | "deposito" | None (não pendente)
        "ocorrencia": r.ocorrencia,
        "entregue": r.entregue,
        "data_comprovante": r.data_comprovante,
        "data_baixa": r.data_baixa,
        "status": r.status,
        "estado": r.estado,
        "previsao": r.previsao,
        "dias_atraso": r.dias_atraso,
        "serie": r.serie,
        "filial": r.filial,
        "cnpj_filial": r.cnpj_filial,
        "notas_fiscais": r.notas_fiscais,
        "tipo_conhecimento": r.tipo_conhecimento,  # NORMAL, REENTREGA, CORTESIA, DEVOLUÇÃO...
        "prazo_comprovante_dias": prazo_dias,
        "distancia_km": distancia_km,
    }


def gravar(db: Session, registros: list[dict], hoje: datetime.date, janela_dias: int, validade: str) -> list[str]:
    """Monta e grava o Painel pronto de cada período que a janela sincronizada
    cobre inteiro. `registros` são os do relatório. Devolve um resumo por período."""
    resumo = []
    for periodo in PERIODOS:
        if periodo + FOLGA_DIAS > janela_dias:
            continue  # a janela sincronizada não cobre esse período inteiro
        cobre_desde = (hoje - datetime.timedelta(days=periodo + FOLGA_DIAS)).isoformat()
        itens = [registro_para_dict(SimpleNamespace(**r)) for r in registros if (r.get("emissao") or "") >= cobre_desde]
        corpo = gzip.compress(json.dumps(itens, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
        # apaga e insere (sem ler o arquivo antigo de volta)
        db.query(PainelSnapshot).filter(PainelSnapshot.periodo == periodo).delete(synchronize_session=False)
        db.add(PainelSnapshot(periodo=periodo, validade=validade, cobre_desde=cobre_desde, registros=len(itens), corpo=corpo))
        resumo.append(f"{periodo}d: {len(itens)} CT-e, {len(corpo) / 1024:.0f} KB")
    db.commit()
    return resumo


def ler(db: Session, validade: str, data_inicial: str | None, data_final: str | None, empresas: set[str] | None):
    """CT-e do período, do escopo de quem pediu, a partir do Painel pronto:
    `(lista de dicts, bytes lidos do banco)`. `None` = não há Painel pronto que
    sirva (o chamador usa as linhas de `cargas`)."""
    if not validade or not data_inicial:
        return None
    try:
        escolhido = (
            db.query(PainelSnapshot.periodo)
            .filter(PainelSnapshot.validade == validade, PainelSnapshot.cobre_desde <= data_inicial)
            .order_by(PainelSnapshot.cobre_desde.desc())  # o menor que ainda cobre o pedido
            .first()
        )
        if not escolhido:
            return None
        corpo = db.query(PainelSnapshot.corpo).filter(PainelSnapshot.periodo == escolhido[0]).scalar()
        dados = json.loads(gzip.decompress(corpo))
    except Exception:
        db.rollback()
        logger.exception("Painel pronto indisponível -- usando as linhas de cargas.")
        return None

    # mesma regra de escopo.filtro_cargas: UPPER(TRIM(tomador ou remetente)) na lista do analista
    lista = set(empresas) or {""} if empresas is not None else None
    saida = []
    for r in dados:
        emissao = r.get("emissao") or ""
        if emissao < data_inicial or (data_final and emissao > data_final):
            continue
        if lista is not None and not ({(r.get("consignatario") or "").strip().upper(), (r.get("remetente") or "").strip().upper()} & lista):
            continue
        saida.append(r)
    return saida, len(corpo)


def contar_uso(db: Session, rotulo: str, nbytes: int) -> None:
    """Soma uma leitura em meta 'uso:AAAA-MM-DD:<rotulo>' ("chamadas|bytes").
    Nunca atrapalha a resposta se falhar."""
    try:
        chave = f"uso:{datetime.date.today().isoformat()}:{rotulo}"[:50]
        m = db.get(Meta, chave)
        chamadas, total = 0, 0
        if m and m.valor and "|" in m.valor:
            chamadas, total = (int(x) for x in m.valor.split("|", 1))
        valor = f"{chamadas + 1}|{total + int(nbytes)}"
        if m:
            m.valor = valor
        else:
            db.add(Meta(chave=chave, valor=valor))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Não foi possível registrar o uso (%s).", rotulo)
