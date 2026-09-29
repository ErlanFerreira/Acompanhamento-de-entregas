"""Relatórios gerados a partir da tela de Bipagem, preenchendo os modelos em
`app/modelos/` (cópias limpas das planilhas que o SAC já usava -- mantêm
logo, cores, fórmulas e layout de impressão):

- Controle de comprovantes (`controle_comprovantes.xlsx`): todos os CT-e do
  mês de um tomador; os bipados como "comprovante" ganham a data do bip em
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
from sqlalchemy.orm import Session

from . import chave_cte
from .filiais import dados_filial, fmt_cnpj
from .models import Bipagem, Carga

MODELOS = Path(__file__).parent / "modelos"
# Brasília sem horário de verão (abolido em 2019) -- evita depender do
# pacote tzdata, que não vem no Windows.
FUSO_BR = datetime.timezone(datetime.timedelta(hours=-3))

FINALIDADES = ("comprovante", "fatura")


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


def gerar_controle_comprovantes(db: Session, cnpj_raiz: str, ano: int, mes: int, responsavel: str) -> bytes:
    inicio = datetime.date(ano, mes, 1)
    fim = datetime.date(ano + (mes == 12), mes % 12 + 1, 1)
    cargas = (
        db.query(Carga)
        .filter(
            Carga.cnpj_consignatario.like(f"{cnpj_raiz}%"),
            Carga.emissao >= inicio.isoformat(),
            Carga.emissao < fim.isoformat(),
        )
        .order_by(Carga.emissao.asc(), Carga.cte.asc())
        .all()
    )

    # Primeiro bip de comprovante de cada CT-e -- (filial, número) -> bip.
    bips: dict[tuple[str, int], Bipagem] = {}
    filiais = {c.cnpj_filial for c in cargas}
    if filiais:
        for b in (
            db.query(Bipagem)
            .filter(Bipagem.finalidade == "comprovante", Bipagem.cnpj_filial.in_(filiais))
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


def tomadores_disponiveis(db: Session) -> list[dict]:
    """Tomadores (agrupados pela raiz do CNPJ -- matriz + filiais do
    parceiro) com CT-e na base, pro seletor do controle mensal."""
    grupos: dict[str, dict] = {}
    for cnpj, nome in db.query(Carga.cnpj_consignatario, Carga.consignatario).filter(Carga.cnpj_consignatario.isnot(None)):
        raiz = cnpj[:8]
        g = grupos.setdefault(raiz, {"cnpj_raiz": raiz, "nome": nome, "total": 0})
        g["total"] += 1
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
