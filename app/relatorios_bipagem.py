"""Relatórios gerados a partir da tela de Bipagem, preenchendo os modelos em
`app/modelos/` (cópias limpas das planilhas que o SAC já usava -- mantêm
logo, cores, fórmulas e layout de impressão):

- Controle de comprovantes (`controle_comprovantes.xlsx`): todos os CT-e do
  mês de um tomador; os bipados ganham a data do (primeiro) bip em
  "Data de Envio ao Financeiro/Arquivo" e viram "Recebido".
- Protocolo de envio de faturas (`protocolo_envio.xlsx`): lista "DACTE Nº /
  Nota Fiscal" dos CT-e bipados, uma aba por filial + tomador (o recebedor).
"""

import copy
import datetime
import io
from pathlib import Path

import openpyxl
from openpyxl.drawing.image import Image
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.formatting.formatting import ConditionalFormattingList
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from . import chave_cte
from .filiais import dados_filial, fmt_cnpj
from .models import Bipagem, Carga, ProtocoloFatura

MODELOS = Path(__file__).parent / "modelos"
# Brasília sem horário de verão (abolido em 2019) -- evita depender do
# pacote tzdata, que não vem no Windows.
FUSO_BR = datetime.timezone(datetime.timedelta(hours=-3))

# Um bip serve pros dois documentos. Registros antigos podem ter
# "comprovante"/"fatura" (quando as listas eram separadas) -- as consultas
# ignoram a finalidade.
FINALIDADE_GERAL = "geral"


def data_br(dt_utc: datetime.datetime) -> datetime.date:
    return dt_utc.replace(tzinfo=datetime.timezone.utc).astimezone(FUSO_BR).date()


def _serie_confere(carga: Carga, dados: dict) -> bool:
    # Série só vem se o relatório do GW tiver essa coluna. Minuta no GW é
    # série "M"; na chave impressa a série é numérica, então aceita os dois.
    if not carga.serie:
        return True
    serie = carga.serie.strip().upper()
    if dados["modelo"] == chave_cte.MODELO_MINUTA:
        return serie in ("M", dados["serie"])
    return serie == dados["serie"]


def buscar_carga(db: Session, dados: dict) -> Carga | None:
    """Acha o CT-e/minuta da chave decodificada no banco -- mesma filial e
    número; havendo mais de um (CT-e e minuta com o mesmo número), a série
    decide quando o relatório a traz."""
    # No banco o número vem como no relatório do GW (ex: "088375", 6 dígitos
    # com zeros à esquerda); na chave são 9 dígitos -- testa as variações.
    numero = dados["numero"]
    candidatos = {str(numero)} | {str(numero).zfill(w) for w in range(6, 10)}
    cargas = (
        db.query(Carga)
        .filter(Carga.cnpj_filial == dados["cnpj_emitente"], Carga.cte.in_(candidatos))
        .all()
    )
    # Os que têm série (e ela confere) antes dos sem série, gravados antes
    # do relatório trazer essa coluna.
    cargas.sort(key=lambda c: c.serie is None)
    return next((c for c in cargas if _serie_confere(c, dados)), None)


def _data(iso: str | None):
    return datetime.date.fromisoformat(iso) if iso else None


def _int_se_numero(v):
    texto = str(v or "").strip()
    return int(texto) if texto.isdigit() else (texto or None)


def _salvar(wb) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------ controle de comprovantes ----

_LINHA_INICIAL = 9
_NUM_COLUNAS = 17  # A..Q


def gerar_controle_comprovantes(db: Session, tomador: str, ano: int, mes: int, responsavel: str) -> bytes:
    """`tomador` é o `id` de `tomadores_disponiveis`: raiz do CNPJ (8
    dígitos) ou "nome:<nome>" pra CT-e ainda sem CNPJ do tomador."""
    inicio = datetime.date(ano, mes, 1)
    fim = datetime.date(ano + (mes == 12), mes % 12 + 1, 1)
    if tomador.startswith(PREFIXO_TOMADOR_NOME):
        nome = tomador[len(PREFIXO_TOMADOR_NOME):]
        filtro_tomador = Carga.consignatario == nome
    else:
        # CT-e ainda sem CNPJ do tomador entram pelo nome, se for o mesmo
        # nome que esse CNPJ tem nos CT-e que já têm.
        nomes = [
            n for (n,) in db.query(Carga.consignatario)
            .filter(Carga.cnpj_consignatario.like(f"{tomador}%"))
            .distinct()
            if n
        ]
        filtro_tomador = or_(
            Carga.cnpj_consignatario.like(f"{tomador}%"),
            and_(Carga.cnpj_consignatario.is_(None), Carga.consignatario.in_(nomes)),
        )
    cargas = (
        db.query(Carga)
        .filter(
            filtro_tomador,
            Carga.emissao >= inicio.isoformat(),
            Carga.emissao < fim.isoformat(),
        )
        .order_by(Carga.emissao.asc(), Carga.cte.asc())
        .all()
    )

    # Primeiro bip de cada CT-e -- (filial, número) -> bip.
    bips: dict[tuple[str, int], Bipagem] = {}
    filiais = {c.cnpj_filial for c in cargas}
    if filiais:
        for b in (
            db.query(Bipagem)
            .filter(Bipagem.cnpj_filial.in_(filiais))
            .order_by(Bipagem.bipado_em.asc())
        ):
            bips.setdefault((b.cnpj_filial, b.numero), b)

    wb = openpyxl.load_workbook(MODELOS / "controle_comprovantes.xlsx", rich_text=True)
    ws = wb.active
    ws["G4"] = inicio
    ws["G5"] = responsavel or None

    estilos = [ws.cell(row=_LINHA_INICIAL, column=c) for c in range(1, _NUM_COLUNAS + 1)]
    for i, carga in enumerate(cargas):
        r = _LINHA_INICIAL + i
        numero = int(carga.cte) if (carga.cte or "").isdigit() else None
        bip = bips.get((carga.cnpj_filial, numero))
        valores = [
            carga.cte,
            carga.serie or (bip.serie if bip else None),
            carga.filial or dados_filial(carga.cnpj_filial)["apelido"],
            _data(carga.emissao),
            _data(carga.previsao),
            fmt_cnpj(carga.cnpj_consignatario),
            carga.consignatario,
            carga.remetente,
            carga.destinatario,
            carga.cidade_dest,
            carga.uf_dest,
            carga.notas_fiscais,
            carga.cte_redespacho,
            data_br(bip.bipado_em) if bip else None,
            f'=IF(ISBLANK($N{r}), "Pendente", "Recebido")',
            None,
            None,
        ]
        for c, (valor, estilo) in enumerate(zip(valores, estilos), start=1):
            celula = ws.cell(row=r, column=c, value=valor)
            if r != _LINHA_INICIAL:
                celula.font = copy.copy(estilo.font)
                celula.border = copy.copy(estilo.border)
                celula.fill = copy.copy(estilo.fill)
                celula.alignment = copy.copy(estilo.alignment)
                celula.number_format = estilo.number_format

    ultima = _LINHA_INICIAL + max(len(cargas), 1) - 1
    ws.auto_filter.ref = f"A8:Q{ultima}"

    # A formatação condicional do modelo (verde "Recebido" / vermelho
    # "Pendente") cobre um intervalo fixo -- refaz pro tamanho real.
    regras = [regra for cf in ws.conditional_formatting for regra in cf.rules]
    ws.conditional_formatting = ConditionalFormattingList()
    for regra in regras:
        texto = regra.text or ""
        regra.formula = [f'NOT(ISERROR(SEARCH("{texto}",O{_LINHA_INICIAL})))']
        ws.conditional_formatting.add(f"O{_LINHA_INICIAL}:O{ultima}", regra)

    return _salvar(wb)


PREFIXO_TOMADOR_NOME = "nome:"


def tomadores_disponiveis(db: Session) -> list[dict]:
    """Tomadores com CT-e na base, pro seletor do controle mensal --
    agrupados pela raiz do CNPJ (matriz + filiais do parceiro). CT-e ainda
    sem CNPJ do tomador (sincronizados antes dessa coluna existir) entram
    pelo nome, com `id` "nome:<nome>"."""
    linhas = (
        db.query(Carga.cnpj_consignatario, Carga.consignatario, func.count())
        .group_by(Carga.cnpj_consignatario, Carga.consignatario)
        .all()
    )
    grupos: dict[str, dict] = {}
    nomes_com_cnpj: set[str] = set()
    for cnpj, nome, total in linhas:
        if cnpj:
            raiz = cnpj[:8]
            g = grupos.setdefault(raiz, {"id": raiz, "cnpj_raiz": raiz, "nome": nome, "total": 0})
            g["total"] += total
            nomes_com_cnpj.add((nome or "").strip().upper())
    for cnpj, nome, total in linhas:
        nome_norm = (nome or "").strip().upper()
        if cnpj or not nome_norm or nome_norm in nomes_com_cnpj:
            continue
        chave = PREFIXO_TOMADOR_NOME + nome.strip()
        g = grupos.setdefault(chave, {"id": chave, "cnpj_raiz": None, "nome": nome.strip(), "total": 0})
        g["total"] += total
    return sorted(grupos.values(), key=lambda g: (g["nome"] or "").upper())


# ------------------------------------------------ protocolo de faturas ----

# Cada página do modelo tem 34 linhas com dois pares "DACTE Nº / NOTA
# FISCAL" (colunas B/C e D/E); são duas páginas por aba.
_SLOTS_PROTOCOLO = (
    [(r, 2) for r in range(11, 45)]
    + [(r, 4) for r in range(11, 45)]
    + [(r, 2) for r in range(55, 89)]
    + [(r, 4) for r in range(55, 89)]
)
# Como o emitente aparece no modelo (que foi feito pra matriz).
_EMITENTE_MODELO = {
    "razao_social": "G. FREIRE BEZERRA DE MORAIS",
    "cnpj": "20.343.618/0001-61",
    "endereco": "R. Jose Jaime Coutinho Dias, 705 a 715 - Bairro Novo / Carpina",
}


def _substituir(celula, antigo: str, novo: str):
    valor = celula.value
    if isinstance(valor, CellRichText):
        # Monta blocos novos em vez de alterar os existentes: `copy_worksheet`
        # compartilha o mesmo objeto entre as abas copiadas.
        blocos = []
        for bloco in valor:
            if isinstance(bloco, TextBlock):
                texto = bloco.text.replace(antigo, novo)
                if texto:
                    blocos.append(TextBlock(copy.copy(bloco.font), texto))
            elif bloco:
                blocos.append(bloco.replace(antigo, novo))
        celula.value = CellRichText(blocos)
    elif isinstance(valor, str):
        celula.value = valor.replace(antigo, novo)


def _item_protocolo(db: Session, chave: str) -> dict | None:
    try:
        dados = chave_cte.decodificar(chave)
    except chave_cte.ChaveInvalida:
        return None
    carga = buscar_carga(db, dados)
    return {
        "cnpj_filial": dados["cnpj_emitente"],
        # DACTE Nº = conhecimento do parceiro; sem redespacho, o nosso.
        "dacte": _int_se_numero(carga.cte_redespacho) if carga and carga.cte_redespacho else dados["numero"],
        "nf": _int_se_numero(carga.notas_fiscais) if carga else None,
        "tomador_cnpj": carga.cnpj_consignatario if carga else None,
        "tomador_nome": (carga.consignatario if carga else None) or "TOMADOR NÃO IDENTIFICADO",
        "tomador_endereco": carga.endereco_consignatario if carga else None,
        "tomador_cidade": carga.cidade_cons if carga else None,
    }


def _copiar_imagens(origem, destino):
    for img in origem._images:
        # `_data()` do openpyxl fecha o buffer da imagem depois de ler --
        # repõe um novo na original, senão o save falha ("closed file").
        dados = img._data()
        img.ref = io.BytesIO(dados)
        nova = Image(io.BytesIO(dados))
        nova.anchor = copy.deepcopy(img.anchor)
        nova.width, nova.height = img.width, img.height
        destino.add_image(nova)


def _titulo_aba(nome: str, usados: set[str]) -> str:
    base = "".join(c for c in nome if c not in "[]:*?/\\")[:28].strip() or "Protocolo"
    titulo, n = base, 2
    while titulo in usados:
        titulo = f"{base[:25]} ({n})"
        n += 1
    usados.add(titulo)
    return titulo


def gerar_protocolo_faturas(db: Session, chaves: list[str]) -> bytes:
    grupos: dict[tuple, list[dict]] = {}
    for chave in chaves:
        item = _item_protocolo(db, chave)
        if item:
            k = (item["cnpj_filial"], item["tomador_cnpj"] or item["tomador_nome"])
            grupos.setdefault(k, []).append(item)

    capacidade = len(_SLOTS_PROTOCOLO)
    folhas = [
        itens[i:i + capacidade]
        for itens in grupos.values()
        for i in range(0, len(itens), capacidade)
    ] or [[]]

    wb = openpyxl.load_workbook(MODELOS / "protocolo_envio.xlsx", rich_text=True)
    base = wb.active
    # Copia a aba modelo ainda vazia antes de preencher qualquer uma.
    abas = [base]
    for _ in folhas[1:]:
        nova = wb.copy_worksheet(base)
        _copiar_imagens(base, nova)
        nova.page_setup.orientation = base.page_setup.orientation
        nova.page_setup.paperSize = base.page_setup.paperSize
        nova.sheet_properties.pageSetUpPr = copy.copy(base.sheet_properties.pageSetUpPr)
        abas.append(nova)

    titulos: set[str] = set()
    for ws, itens in zip(abas, folhas):
        primeiro = itens[0] if itens else {}
        filial = dados_filial(primeiro.get("cnpj_filial"))
        razao = filial["razao_social"] or filial["apelido"]

        ws.title = _titulo_aba(primeiro.get("tomador_nome") or "Protocolo", titulos)
        ws["C2"] = primeiro.get("tomador_nome")
        ws["C3"] = primeiro.get("tomador_endereco")
        ws["C4"] = fmt_cnpj(primeiro.get("tomador_cnpj")) or None
        ws["E4"] = primeiro.get("tomador_cidade")
        # Emitente: o modelo já vem com os dados da matriz -- troca só o
        # texto dentro da formatação (negrito "CNPJ:"/"Endereço:", linhas
        # sublinhadas da assinatura), sem perder o rich text da célula.
        _substituir(ws["B6"], _EMITENTE_MODELO["razao_social"], razao)
        _substituir(ws["D6"], _EMITENTE_MODELO["cnpj"], fmt_cnpj(filial["cnpj"]))
        _substituir(ws["B7"], _EMITENTE_MODELO["endereco"], filial["endereco"])
        for coord in ("B45", "B89"):
            _substituir(ws[coord], _EMITENTE_MODELO["razao_social"], razao)

        for (linha, coluna), item in zip(_SLOTS_PROTOCOLO, itens):
            ws.cell(row=linha, column=coluna, value=item["dacte"])
            ws.cell(row=linha, column=coluna + 1, value=item["nf"])

    return _salvar(wb)


# ----------------------------------- controle de faturamento (protocolos) ----
#
# Os comprovantes chegam todo dia e são bipados conforme chegam; o protocolo
# de faturas só deve levar os que ainda não foram faturados. Cada protocolo
# gerado marca os bips que entraram nele (`Bipagem.protocolo_id`), e o
# próximo protocolo do mesmo tomador só pega os pendentes.

SEM_TOMADOR = "sem-tomador"


def id_tomador(carga: Carga | None) -> str:
    """Mesmo `id` de `tomadores_disponiveis`: raiz do CNPJ ou "nome:<nome>"."""
    if carga is None:
        return SEM_TOMADOR
    if carga.cnpj_consignatario:
        return carga.cnpj_consignatario[:8]
    if (carga.consignatario or "").strip():
        return PREFIXO_TOMADOR_NOME + carga.consignatario.strip()
    return SEM_TOMADOR


def _pendentes_por_tomador(db: Session) -> dict[str, dict]:
    grupos: dict[str, dict] = {}
    bips = (
        db.query(Bipagem)
        .filter(Bipagem.protocolo_id.is_(None))
        .order_by(Bipagem.bipado_em.asc())
        .all()
    )
    for bip in bips:
        try:
            dados = chave_cte.decodificar(bip.chave)
        except chave_cte.ChaveInvalida:
            continue
        carga = buscar_carga(db, dados)
        tid = id_tomador(carga)
        nome = (carga.consignatario if carga else None) or "TOMADOR NÃO IDENTIFICADO (fora da base)"
        g = grupos.setdefault(tid, {
            "id": tid,
            "nome": nome.strip(),
            "cnpj_raiz": carga.cnpj_consignatario[:8] if carga and carga.cnpj_consignatario else None,
            "quantidade": 0,
            "bips": [],
        })
        g["quantidade"] += 1
        g["bips"].append(bip)
    return grupos


def faturas_pendentes(db: Session) -> list[dict]:
    """CT-e bipados que ainda não entraram em nenhum protocolo, por tomador."""
    grupos = _pendentes_por_tomador(db)
    return sorted(
        ({k: v for k, v in g.items() if k != "bips"} for g in grupos.values()),
        key=lambda g: (g["nome"] or "").upper(),
    )


def criar_protocolo_faturas(db: Session, tomador: str) -> ProtocoloFatura | None:
    """Gera o protocolo com os CT-e pendentes do tomador e dá baixa neles.
    Retorna `None` se não houver pendentes."""
    grupo = _pendentes_por_tomador(db).get(tomador)
    if not grupo:
        return None

    protocolo = ProtocoloFatura(tomador_id=tomador, tomador_nome=grupo["nome"], quantidade=0)
    db.add(protocolo)
    db.flush()
    # Só marca os que continuam pendentes -- se outra pessoa gerou um
    # protocolo ao mesmo tempo, os que ela já pegou ficam de fora deste.
    ids = [b.id for b in grupo["bips"]]
    db.query(Bipagem).filter(Bipagem.id.in_(ids), Bipagem.protocolo_id.is_(None)).update(
        {Bipagem.protocolo_id: protocolo.id}, synchronize_session=False
    )
    chaves = [
        b.chave for b in db.query(Bipagem)
        .filter(Bipagem.protocolo_id == protocolo.id)
        .order_by(Bipagem.bipado_em.asc())
    ]
    if not chaves:
        db.rollback()
        return None
    protocolo.quantidade = len(chaves)
    protocolo.arquivo = gerar_protocolo_faturas(db, chaves)
    db.commit()
    return protocolo


def desfazer_protocolo(db: Session, protocolo_id: int) -> bool:
    """Apaga o protocolo e devolve os CT-e dele pra lista de pendentes."""
    protocolo = db.query(ProtocoloFatura).filter(ProtocoloFatura.id == protocolo_id).first()
    if not protocolo:
        return False
    db.query(Bipagem).filter(Bipagem.protocolo_id == protocolo_id).update(
        {Bipagem.protocolo_id: None}, synchronize_session=False
    )
    db.delete(protocolo)
    db.commit()
    return True
