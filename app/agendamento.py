"""Agendamento de entregas por WhatsApp (clientes que exigem contato antes da
entrega -- hoje a Fotus). O telefone do recebedor vem na observação da NF
(informações complementares, `infCpl` no XML); a analista importa o XML da
nota (ou cola a observação), o sistema acha o telefone, junta com o CT-e e a
previsão de entrega já sincronizados e monta a mensagem. O envio é pelo link
wa.me (fase 1): abre o WhatsApp da analista com o texto pronto, ela só
confere e envia. A resposta do cliente é marcada na tela.

Mora no banco separado (CONSULTAS_DATABASE_URL, com o principal de reserva),
com metadata e sessão próprias, como o Controle GDC.
"""

import datetime
import json
import re
import threading
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Column, Date, DateTime, Integer, String, Text, UniqueConstraint, func, or_
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from . import db as banco
from .models import Carga
from .security import exigir_tela_api, usuario_logado

engine_ag = banco.engine_separado
AgBase = declarative_base()
SessionAg = sessionmaker(bind=engine_ag, autoflush=False, autocommit=False)

BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))

# Remetentes cujas entregas são agendadas por telefone (nome como vem do GW).
CLIENTES = ("FOTUS",)

# A enviar -> aguardando resposta -> confirmado | reagendar (pediu outra data)
# | sem_contato (telefone errado / não responde). "concluido" = sai da lista.
SITUACOES = ("a_enviar", "aguardando", "confirmado", "reagendar", "sem_contato", "concluido")

router = APIRouter()


class Agendamento(AgBase):
    __tablename__ = "agendamentos_entrega"
    __table_args__ = (UniqueConstraint("nf", "cnpj_emitente", name="uq_agendamento_nf_emitente"),)

    id = Column(Integer, primary_key=True)
    nf = Column(String(20), nullable=False, index=True)  # sem zeros à esquerda
    cnpj_emitente = Column(String(14), nullable=False, default="")
    emitente = Column(String(200), nullable=False, default="")
    chave = Column(String(44), nullable=True)
    id_cte = Column(String(80), nullable=True, index=True)  # mesma chave de Carga.id_cte
    cte = Column(String(50), nullable=True)
    cliente = Column(String(200), nullable=False, default="")
    cidade = Column(String(120), nullable=False, default="")
    uf = Column(String(2), nullable=False, default="")
    telefone = Column(String(20), nullable=False, default="")  # 55 + DDD + número, só dígitos
    telefones = Column(Text, nullable=False, default="[]")  # JSON: todos os achados
    observacao_nf = Column(Text, nullable=False, default="")
    data_prevista = Column(Date, nullable=True)
    situacao = Column(String(20), nullable=False, default="a_enviar", index=True)
    data_combinada = Column(Date, nullable=True)  # a que o cliente pediu (reagendar)
    anotacao = Column(Text, nullable=False, default="")
    envios = Column(Integer, nullable=False, default=0)
    enviado_em = Column(DateTime, nullable=True)
    enviado_por = Column(String(120), nullable=True)
    respondido_em = Column(DateTime, nullable=True)
    respondido_por = Column(String(120), nullable=True)
    criado_em = Column(DateTime, nullable=False)
    criado_por = Column(String(120), nullable=False, default="")
    atualizado_em = Column(DateTime, nullable=True)


_tabelas_ok = False
_tabelas_lock = threading.Lock()


def preparar_tabelas() -> None:
    global _tabelas_ok
    if not _tabelas_ok:
        with _tabelas_lock:
            if not _tabelas_ok:
                AgBase.metadata.create_all(bind=engine_ag)
                _tabelas_ok = True


def get_ag_db():
    """As tabelas são criadas na primeira vez que alguém usa a tela (a função
    da Vercel pode subir sem rodar o seed). A tela abre fazendo duas chamadas
    ao mesmo tempo -- a trava evita as duas tentarem criar a tabela juntas."""
    preparar_tabelas()
    db = SessionAg()
    try:
        yield db
    finally:
        db.close()


def _agora() -> datetime.datetime:
    return datetime.datetime.now(BRASILIA).replace(tzinfo=None)


def _quem(request: Request) -> str:
    u = usuario_logado(request)
    return (u.get("nome") or u.get("login") or "Analista")[:120] if u else "Supervisor"


# ------------------------------------------------------------ telefones ----

# DDDs que existem no Brasil -- descarta números que só parecem telefone.
DDDS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99,
}

# +55 (opcional), DDD com ou sem parênteses/zero, número de 8 ou 9 dígitos com
# ou sem separadores. Sem dígito colado antes/depois -- não pega pedaço de
# CNPJ, CPF, chave de acesso, CEP, número de pedido...
_FONE_RE = re.compile(
    r"(?<!\d)(?:\+?\s?55[\s.-]*)?\(?\s?0?(\d{2})\s?\)?[\s.-]*(9?[\s.]?\d{4})[\s.-]*(\d{4})(?!\d)"
)
# Palavras que indicam que o número é de contato (ganham prioridade).
_PISTA_RE = re.compile(r"(?i)whats|zap|wpp|cel|fone|tel|contato|recebedor|respons|ligar")


def normalizar_telefone(valor: str) -> str | None:
    """Qualquer forma de telefone -> "55DDNNNNNNNNN" (só dígitos), ou None."""
    d = re.sub(r"\D", "", str(valor or ""))
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if d.startswith("0") and len(d) in (11, 12):
        d = d[1:]
    if len(d) not in (10, 11) or int(d[:2]) not in DDDS:
        return None
    numero = d[2:]
    if len(numero) == 9 and numero[0] != "9":
        return None
    if len(numero) == 8 and numero[0] in "6789":
        numero = "9" + numero  # celular escrito sem o 9 da frente
    return "55" + d[:2] + numero


def eh_celular(fone: str) -> bool:
    return len(fone) == 13 and fone[4] == "9"


def extrair_telefones(texto: str) -> list[str]:
    """Telefones achados no texto, o mais provável de ter WhatsApp primeiro:
    celular antes de fixo; perto de "whats/cel/contato" antes dos outros."""
    achados = []
    for m in _FONE_RE.finditer(texto or ""):
        fone = normalizar_telefone("".join(m.groups()))
        if not fone:
            continue
        perto = (texto[max(0, m.start() - 30):m.start()])
        achados.append((fone, eh_celular(fone), bool(_PISTA_RE.search(perto)), m.start()))
    vistos, saida = set(), []
    for fone, cel, pista, pos in sorted(achados, key=lambda a: (not a[1], not a[2], a[3])):
        if fone not in vistos:
            vistos.add(fone)
            saida.append(fone)
    return saida


# ----------------------------------------------------------------- XML ----

def _txt(no, caminho: str) -> str:
    achado = no.find(caminho) if no is not None else None
    return (achado.text or "").strip() if achado is not None and achado.text else ""


def ler_xml_nfe(conteudo: str) -> dict:
    """Dados do XML da NF-e (nfeProc ou NFe) usados no agendamento."""
    if len(conteudo) > 2_000_000 or "<!ENTITY" in conteudo:
        raise ValueError("arquivo inválido")
    raiz = ET.fromstring(conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo)
    inf = raiz if raiz.tag.endswith("infNFe") else raiz.find(".//{*}infNFe")
    if inf is None:
        raise ValueError("não é XML de NF-e")
    ide, emit, dest = inf.find("{*}ide"), inf.find("{*}emit"), inf.find("{*}dest")
    ender = dest.find("{*}enderDest") if dest is not None else None
    chave = re.sub(r"\D", "", inf.get("Id") or "")
    obs = " | ".join(t for t in (_txt(inf, "{*}infAdic/{*}infCpl"), _txt(inf, "{*}infAdic/{*}infAdFisco")) if t)
    return {
        "nf": _txt(ide, "{*}nNF").lstrip("0"),
        "chave": chave if len(chave) == 44 else None,
        "cnpj_emitente": _txt(emit, "{*}CNPJ"),
        "emitente": _txt(emit, "{*}xNome"),
        "cliente": _txt(dest, "{*}xNome"),
        "cidade": _txt(ender, "{*}xMun"),
        "uf": _txt(ender, "{*}UF"),
        "fone_dest": _txt(ender, "{*}fone"),
        "observacao_nf": obs,
    }


# ---------------------------------------------------- CT-e sincronizado ----

def _nfs_da_carga(c: Carga) -> set[str]:
    return {n.lstrip("0") for n in re.findall(r"\d+", c.notas_fiscais or "")}


def achar_carga(db: Session, nf: str, cnpj_emitente: str = "") -> Carga | None:
    """CT-e sincronizado que transporta a NF -- do emitente certo quando dá pra
    saber (o mesmo número de NF existe em empresas diferentes), o mais recente
    primeiro (reentrega/devolução geram outro CT-e com a mesma NF)."""
    if not nf:
        return None
    candidatas = (db.query(Carga).filter(Carga.notas_fiscais.like(f"%{nf}%"))
                  .order_by(Carga.emissao.desc()).limit(30).all())
    candidatas = [c for c in candidatas if nf in _nfs_da_carga(c)]
    if cnpj_emitente:
        mesmo = [c for c in candidatas if re.sub(r"\D", "", c.cnpj_remetente or "") == cnpj_emitente]
        candidatas = mesmo or [c for c in candidatas if not c.cnpj_remetente]
    pendentes = [c for c in candidatas if not c.entregue]
    return (pendentes or candidatas or [None])[0]


def _data(valor) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(valor or "")[:10])
    except ValueError:
        return None


def _carga_dict(c: Carga | None) -> dict | None:
    if not c:
        return None
    return {"id_cte": c.id_cte, "cte": c.cte, "previsao": c.previsao, "entregue": bool(c.entregue),
            "data_entrega": c.data_baixa or c.data_comprovante, "status": c.status, "remetente": c.remetente,
            "destinatario": c.destinatario, "cidade": c.cidade_dest, "uf": c.uf_dest}


def eh_cliente_agendado(nome_emitente: str) -> bool:
    """NF de um remetente que exige agendamento (CLIENTES)?"""
    nome = (nome_emitente or "").upper()
    return any(c in nome for c in CLIENTES)


def _filtro_clientes():
    return or_(*[func.upper(Carga.remetente).like(f"%{c}%") for c in CLIENTES])


# --------------------------------------------------------------- dados ----

def _to_dict(a: Agendamento, carga: Carga | None = None) -> dict:
    return {
        "id": a.id, "nf": a.nf, "cnpj_emitente": a.cnpj_emitente, "emitente": a.emitente, "chave": a.chave,
        "id_cte": a.id_cte, "cte": a.cte, "cliente": a.cliente, "cidade": a.cidade, "uf": a.uf,
        "telefone": a.telefone, "telefones": json.loads(a.telefones or "[]"), "observacao_nf": a.observacao_nf,
        "data_prevista": a.data_prevista.isoformat() if a.data_prevista else None,
        "situacao": a.situacao,
        "data_combinada": a.data_combinada.isoformat() if a.data_combinada else None,
        "anotacao": a.anotacao, "envios": a.envios,
        "enviado_em": a.enviado_em.isoformat() if a.enviado_em else None, "enviado_por": a.enviado_por,
        "respondido_em": a.respondido_em.isoformat() if a.respondido_em else None,
        "respondido_por": a.respondido_por,
        "criado_em": a.criado_em.isoformat() if a.criado_em else None, "criado_por": a.criado_por,
        "carga": _carga_dict(carga),
    }


def gravar(db_ag: Session, db: Session, quem: str, dados: dict) -> tuple[Agendamento, bool]:
    """Cria (ou completa, se a NF já foi importada) um agendamento. Devolve
    (agendamento, novo?). Usado pela tela e pela importação automática da
    pasta de XML (scripts/importar_xml_agendamento.py)."""
    nf = re.sub(r"\D", "", dados.get("nf") or "").lstrip("0")[:20]
    cnpj = re.sub(r"\D", "", dados.get("cnpj_emitente") or "")[:14]
    a = db_ag.query(Agendamento).filter(Agendamento.nf == nf, Agendamento.cnpj_emitente == cnpj).first()
    novo = a is None
    if novo:
        a = Agendamento(nf=nf, cnpj_emitente=cnpj, criado_em=_agora(), criado_por=quem[:120], situacao="a_enviar")
        db_ag.add(a)
    obs = str(dados.get("observacao_nf") or "").strip()[:4000]
    fones = extrair_telefones(obs)
    extra = normalizar_telefone(dados.get("fone_dest") or "")
    if extra and extra not in fones:
        fones.append(extra)
    for campo, limite in (("emitente", 200), ("cliente", 200), ("cidade", 120), ("uf", 2)):
        valor = " ".join(str(dados.get(campo) or "").split())[:limite]
        if valor:
            setattr(a, campo, valor)
    if dados.get("chave"):
        a.chave = dados["chave"]
    if obs:
        a.observacao_nf = obs
        a.telefones = json.dumps(fones)
    if fones and not a.telefone:
        a.telefone = fones[0]
    carga = achar_carga(db, nf, cnpj)
    if carga:
        a.id_cte, a.cte = carga.id_cte, carga.cte
        if not a.cliente:
            a.cliente = carga.destinatario or ""
        if not a.cidade:
            a.cidade = carga.cidade_dest or ""
        if not a.uf:
            a.uf = carga.uf_dest or ""
        if a.data_prevista is None:
            a.data_prevista = _data(carga.previsao)
    if not a.data_prevista and _data(dados.get("data_prevista")):
        a.data_prevista = _data(dados.get("data_prevista"))
    a.atualizado_em = _agora()
    return a, novo


def _cargas_por_id(db: Session, ids: list[str]) -> dict[str, Carga]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {c.id_cte: c for c in db.query(Carga).filter(Carga.id_cte.in_(ids)).all()}


# ---------------------------------------------------------------- rotas ----

TELA = "agendamento"


@router.get("/api/agendamento", dependencies=[Depends(exigir_tela_api(TELA))])
def api_lista(situacao: str = "andamento", busca: str = "", db_ag: Session = Depends(get_ag_db),
              db: Session = Depends(banco.get_db)):
    q = db_ag.query(Agendamento)
    if situacao == "andamento":
        q = q.filter(Agendamento.situacao != "concluido")
    elif situacao in SITUACOES:
        q = q.filter(Agendamento.situacao == situacao)
    busca = busca.strip()
    if busca:
        termo, digitos = f"%{busca}%", re.sub(r"\D", "", busca)
        condicoes = [Agendamento.nf.like(termo), Agendamento.cte.like(termo), Agendamento.cliente.ilike(termo),
                     Agendamento.cidade.ilike(termo)]
        if len(digitos) >= 4:
            condicoes.append(Agendamento.telefone.like(f"%{digitos}%"))
        q = q.filter(or_(*condicoes))
    itens = q.order_by(Agendamento.data_prevista.is_(None), Agendamento.data_prevista, Agendamento.id).limit(500).all()
    # NF importada antes do CT-e sincronizar: tenta vincular de novo.
    mudou = False
    for a in itens:
        if not a.id_cte and a.situacao != "concluido":
            c = achar_carga(db, a.nf, a.cnpj_emitente)
            if c:
                a.id_cte, a.cte = c.id_cte, c.cte
                a.data_prevista = a.data_prevista or _data(c.previsao)
                mudou = True
    if mudou:
        db_ag.commit()
    cargas = _cargas_por_id(db, [a.id_cte for a in itens])
    contagem = dict(db_ag.query(Agendamento.situacao, func.count(Agendamento.id)).group_by(Agendamento.situacao).all())
    return {"itens": [_to_dict(a, cargas.get(a.id_cte)) for a in itens],
            "contagem": {s: int(contagem.get(s, 0)) for s in SITUACOES}}


@router.get("/api/agendamento/sem-agendamento", dependencies=[Depends(exigir_tela_api(TELA))])
def api_sem_agendamento(db_ag: Session = Depends(get_ag_db), db: Session = Depends(banco.get_db)):
    """CT-e pendentes dos clientes que agendam e que ainda não têm NF importada
    aqui -- a lista do que falta contatar."""
    cargas = (db.query(Carga).filter(_filtro_clientes(), Carga.entregue.is_(False))
              .order_by(Carga.previsao, Carga.emissao).limit(1000).all())
    ja = {(a.nf) for a in db_ag.query(Agendamento.nf).all()}
    saida = []
    for c in cargas:
        nfs = sorted(_nfs_da_carga(c))
        if nfs and any(n in ja for n in nfs):
            continue
        saida.append({**_carga_dict(c), "nfs": nfs, "cnpj_remetente": re.sub(r"\D", "", c.cnpj_remetente or ""),
                      "emissao": c.emissao})
    return {"itens": saida}


@router.post("/api/agendamento/xml", dependencies=[Depends(exigir_tela_api(TELA))])
def api_importar_xml(request: Request, payload: dict, db_ag: Session = Depends(get_ag_db),
                     db: Session = Depends(banco.get_db)):
    """`{"arquivos": [{"nome": ..., "conteudo": "<xml>"}]}` -- vários XML de NF-e."""
    resultado = []
    for arq in (payload.get("arquivos") or [])[:200]:
        nome = str(arq.get("nome") or "")[:120]
        try:
            dados = ler_xml_nfe(str(arq.get("conteudo") or ""))
        except (ET.ParseError, ValueError) as e:
            resultado.append({"arquivo": nome, "erro": f"não deu pra ler ({e})."})
            continue
        if not dados["nf"]:
            resultado.append({"arquivo": nome, "erro": "XML sem número de NF."})
            continue
        a, novo = gravar(db_ag, db, _quem(request), dados)
        db_ag.flush()
        resultado.append({"arquivo": nome, "nf": a.nf, "novo": novo, "telefone": a.telefone,
                          "vinculado": bool(a.id_cte)})
    db_ag.commit()
    return {"resultado": resultado}


@router.post("/api/agendamento", dependencies=[Depends(exigir_tela_api(TELA))])
def api_criar(request: Request, payload: dict, db_ag: Session = Depends(get_ag_db), db: Session = Depends(banco.get_db)):
    """Lançamento manual: NF + observação colada (e o que mais vier)."""
    if not re.sub(r"\D", "", str(payload.get("nf") or "")):
        return JSONResponse({"erro": "Informe o número da NF."}, status_code=400)
    a, _ = gravar(db_ag, db, _quem(request), payload)
    fone = normalizar_telefone(payload.get("telefone") or "")
    if fone:
        a.telefone = fone
    db_ag.commit()
    return _to_dict(a, _cargas_por_id(db, [a.id_cte]).get(a.id_cte))


@router.post("/api/agendamento/extrair", dependencies=[Depends(exigir_tela_api(TELA))])
def api_extrair(payload: dict):
    """Prévia dos telefones achados num texto (o formulário mostra ao colar)."""
    return {"telefones": extrair_telefones(str(payload.get("texto") or "")[:4000])}


@router.put("/api/agendamento/{ag_id}", dependencies=[Depends(exigir_tela_api(TELA))])
def api_editar(request: Request, ag_id: int, payload: dict, db_ag: Session = Depends(get_ag_db),
               db: Session = Depends(banco.get_db)):
    a = db_ag.get(Agendamento, ag_id)
    if not a:
        return JSONResponse({"erro": "Agendamento não encontrado (pode ter sido excluído)."}, status_code=404)
    if "telefone" in payload:
        bruto = str(payload.get("telefone") or "").strip()
        fone = normalizar_telefone(bruto)
        if bruto and not fone:
            return JSONResponse({"erro": "Telefone inválido -- use DDD + número, ex.: (81) 99999-8888."}, status_code=400)
        a.telefone = fone or ""
    if "observacao_nf" in payload:
        a.observacao_nf = str(payload.get("observacao_nf") or "").strip()[:4000]
        a.telefones = json.dumps(extrair_telefones(a.observacao_nf))
    for campo, limite in (("cliente", 200), ("cidade", 120)):
        if campo in payload:
            setattr(a, campo, " ".join(str(payload.get(campo) or "").split())[:limite])
    if "anotacao" in payload:
        a.anotacao = str(payload.get("anotacao") or "").strip()[:4000]
    for campo in ("data_prevista", "data_combinada"):
        if campo in payload:
            setattr(a, campo, _data(payload.get(campo)))
    situacao = payload.get("situacao")
    if situacao is not None:
        if situacao not in SITUACOES:
            return JSONResponse({"erro": "Situação inválida."}, status_code=400)
        if situacao == "reagendar" and not a.data_combinada:
            return JSONResponse({"erro": "Informe a data que o cliente pediu."}, status_code=400)
        if situacao != a.situacao and situacao in ("confirmado", "reagendar", "sem_contato"):
            a.respondido_em, a.respondido_por = _agora(), _quem(request)
        a.situacao = situacao
    a.atualizado_em = _agora()
    db_ag.commit()
    return _to_dict(a, _cargas_por_id(db, [a.id_cte]).get(a.id_cte))


@router.post("/api/agendamento/{ag_id}/enviado", dependencies=[Depends(exigir_tela_api(TELA))])
def api_enviado(request: Request, ag_id: int, db_ag: Session = Depends(get_ag_db), db: Session = Depends(banco.get_db)):
    """A analista abriu o WhatsApp com a mensagem: passa a aguardar resposta."""
    a = db_ag.get(Agendamento, ag_id)
    if not a:
        return JSONResponse({"erro": "Agendamento não encontrado (pode ter sido excluído)."}, status_code=404)
    a.envios = (a.envios or 0) + 1
    a.enviado_em, a.enviado_por = _agora(), _quem(request)
    if a.situacao in ("a_enviar", "sem_contato", "aguardando"):
        a.situacao = "aguardando"
    a.atualizado_em = _agora()
    db_ag.commit()
    return _to_dict(a, _cargas_por_id(db, [a.id_cte]).get(a.id_cte))


@router.delete("/api/agendamento/{ag_id}", dependencies=[Depends(exigir_tela_api(TELA))])
def api_excluir(ag_id: int, db_ag: Session = Depends(get_ag_db)):
    a = db_ag.get(Agendamento, ag_id)
    if a:
        db_ag.delete(a)
        db_ag.commit()
    return {"ok": True}
