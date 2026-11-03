"""Planilhas (.xlsx) dos relatórios no padrão do Grupo Transrota.

Segue o `Modelo_Planilha_Grupo_Transrota.xlsx`: logotipo no topo, título em
Bebas Neue à direita, faixa laranja/amarela, linha de informações (filtros,
período, atualizado em), cabeçalho azul, linhas zebradas, linha de TOTAL e
configuração de impressão (A4 paisagem, cabeçalho repetido, rodapé com página).

Uso:
    conteudo = criar_planilha("Pendências", "Painel de Entregas",
                              [Coluna("CT-e", 12), Coluna("Emissão", 12, "data")],
                              linhas, info=[("Período:", "últimos 14 dias")])

E, para os relatórios montados na tela (que só existem no navegador), a rota
POST /api/exportar-xlsx recebe as linhas já filtradas e devolve o .xlsx.
"""

import datetime
import gzip
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl.drawing.image import Image
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .security import require_auth_api

LOGO = Path(__file__).parent / "modelos" / "logo_planilha.png"

AZUL, AMARELO, AMARELO_CLARO, LARANJA = "0A1A5C", "FFD400", "FFE600", "FAA61A"
ZEBRA, BORDA, TEXTO, CINZA, TOTAL = "F4F5FA", "D5D9E6", "1F2433", "5C6170", "FFF4C2"
VERDE, VERMELHO = "2E7D32", "C62828"

FMT_DATA = "dd/mm/yyyy"
FMT_DATAHORA = "dd/mm/yyyy hh:mm"
FMT_NUMERO = r"#,##0.00;\(#,##0.00\);\-"
FMT_INTEIRO = "#,##0"

# tipo -> (formato, alinhamento)
_TIPOS = {
    "texto": ("@", "left"),
    "centro": ("General", "center"),
    "data": (FMT_DATA, "center"),
    "datahora": (FMT_DATAHORA, "center"),
    "inteiro": (FMT_INTEIRO, "right"),
    "numero": (FMT_NUMERO, "right"),
}

MAX_LINHAS = 15000  # a resposta da Vercel passa de ~4,5 MB acima disso


@dataclass
class Coluna:
    titulo: str
    largura: float = 14
    tipo: str = "texto"  # texto | centro | data | datahora | inteiro | numero
    total: bool = False  # soma na linha de TOTAL
    cores: dict = field(default_factory=dict)  # valor -> cor hex (texto colorido, como o "Status" do modelo)


def _fonte(**kw) -> Font:
    kw.setdefault("name", "Segoe UI")
    kw.setdefault("size", 10)
    kw.setdefault("color", TEXTO)
    return Font(**kw)


def _preencher(cor: str) -> PatternFill:
    return PatternFill("solid", fgColor=cor)


def _converter(valor, tipo: str):
    """Datas vindas como texto ISO (AAAA-MM-DD[ HH:MM]) viram data de verdade."""
    if valor is None or valor == "":
        return None
    if tipo in ("data", "datahora") and isinstance(valor, str):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", valor)
        if m:
            try:
                a, me, d = int(m[1]), int(m[2]), int(m[3])
                if m[4] is not None and tipo == "datahora":
                    return datetime.datetime(a, me, d, int(m[4]), int(m[5]))
                return datetime.date(a, me, d)
            except ValueError:
                return valor
        return valor
    if tipo in ("inteiro", "numero") and isinstance(valor, str):
        try:
            return float(valor.replace(".", "").replace(",", ".")) if "," in valor else float(valor)
        except ValueError:
            return valor
    if isinstance(valor, datetime.datetime) and valor.tzinfo is not None:
        return valor.replace(tzinfo=None)
    return valor


def criar_planilha(titulo: str, subtitulo: str, colunas: list[Coluna], linhas, info=None,
                   aba: str = "Relatório", total: bool = True) -> bytes:
    """Monta o .xlsx no padrão do modelo. `linhas`: listas de valores na ordem das
    `colunas`. `info`: pares (rótulo, valor) da linha de informações (até 3)."""
    info = list(info or [])
    n = len(colunas)
    ultima = n + 1  # índice (1-based) da última coluna de dados; a A é a margem
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = aba[:31]
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 100
    wb.properties.creator = "Grupo Transrota"
    wb.properties.title = titulo

    ws.column_dimensions["A"].width = 2
    ws.column_dimensions[get_column_letter(ultima + 1)].width = 2
    for i, c in enumerate(colunas):
        ws.column_dimensions[get_column_letter(i + 2)].width = c.largura

    # cabeçalho com duas linhas quando o título não cabe na largura da coluna
    quebra = any(len(c.titulo) > c.largura * 0.95 for c in colunas)
    for linha, altura in {1: 7.5, 2: 33.75, 3: 19.5, 4: 9.75, 5: 7.5, 6: 15, 7: 9.75, 8: 34 if quebra else 24}.items():
        ws.row_dimensions[linha].height = altura

    # --- topo: logotipo à esquerda, título e subtítulo à direita
    if LOGO.exists():
        img = Image(str(LOGO))
        img.width, img.height = 196, 42
        ws.add_image(img, "B2")
    acumulado, fim = 0.0, ultima
    for i, c in enumerate(colunas):
        acumulado += c.largura
        if acumulado >= 114:
            fim = i + 2
            break
    ini = min(max(fim - 3, 2 + _colunas_do_logo(colunas)), fim)  # à direita do logotipo, até 4 colunas
    for linha, texto, fonte in (
        (2, titulo.upper(), Font(name="Bebas Neue", size=26, color=AZUL)),
        (3, subtitulo, _fonte(color=CINZA)),
    ):
        if ini < fim:
            ws.merge_cells(start_row=linha, start_column=ini, end_row=linha, end_column=fim)
        cel = ws.cell(linha, ini, texto)
        cel.font = fonte
        cel.alignment = Alignment(horizontal="right", vertical="center")

    # --- faixa de cores: laranja, amarelo e amarelo claro (2/3/2 colunas no modelo)
    k = max(1, round(n * 2 / 7))
    for i in range(n):
        cor = LARANJA if i < k else (AMARELO_CLARO if i >= n - k else AMARELO)
        ws.cell(4, i + 2).fill = _preencher(cor)

    # --- linha de informações: rótulo (azul, negrito) + valor sublinhado
    pares = max(1, n // 3)
    extras = info[pares:]
    info = info[:pares]
    for j, (rotulo, valor) in enumerate(info):
        col = 2 + j * 3
        r = ws.cell(6, col, rotulo)
        r.font = _fonte(bold=True, color=AZUL)
        r.alignment = Alignment(horizontal="right", vertical="bottom")
        fim_v = min(col + 2, ultima)
        if fim_v > col + 1:
            ws.merge_cells(start_row=6, start_column=col + 1, end_row=6, end_column=fim_v)
        for cc in range(col + 1, fim_v + 1):
            ws.cell(6, cc).border = Border(bottom=Side("thin", color=AZUL))
        v = ws.cell(6, col + 1, valor)
        v.font = _fonte()
        v.alignment = Alignment(horizontal="left", vertical="bottom", shrink_to_fit=False)
        if isinstance(valor, (datetime.date, datetime.datetime)):
            v.number_format = FMT_DATA if not isinstance(valor, datetime.datetime) else FMT_DATAHORA
    if extras:  # não cabem na linha: vão junto do subtítulo
        sub = ws.cell(3, ini)
        sub.value = " · ".join([subtitulo] + [f"{a} {b}" for a, b in extras if b])

    # --- cabeçalho da tabela
    borda = Border(*(Side("thin", color=BORDA),) * 4)
    for i, c in enumerate(colunas):
        h = ws.cell(8, i + 2, c.titulo)
        h.font = _fonte(bold=True, color="FFFFFF")
        h.fill = _preencher(AZUL)
        h.alignment = Alignment(horizontal="left" if c.tipo == "texto" else "center", vertical="center", wrap_text=True)

    # --- dados
    ultima_linha_dados = 8
    for idx, linha in enumerate(linhas):
        r = 9 + idx
        ws.row_dimensions[r].height = 18
        zebra = _preencher(ZEBRA) if idx % 2 else None
        for i, c in enumerate(colunas):
            valor = _converter(linha[i] if i < len(linha) else None, c.tipo)
            cel = ws.cell(r, i + 2)
            formato, alinhar = _TIPOS.get(c.tipo, _TIPOS["texto"])
            if c.tipo == "texto":
                cel.value = valor
                if isinstance(valor, str):  # sempre texto: nada de fórmula (=...) nem número no lugar da chave
                    cel.data_type = "s"
            else:
                cel.value = valor
            cel.number_format = formato
            cel.font = _fonte()
            cel.alignment = Alignment(horizontal=alinhar, vertical="center")
            cel.border = borda
            if zebra:
                cel.fill = zebra
        ultima_linha_dados = r

    # --- cores de status (verde / vermelho / laranja...) por valor da coluna
    for i, c in enumerate(colunas):
        letra = get_column_letter(i + 2)
        for valor, cor in c.cores.items():
            if re.fullmatch(r"[0-9A-Fa-f]{6}", cor or ""):
                v = str(valor).replace('"', '""')
                ws.conditional_formatting.add(
                    f"{letra}9:{letra}{max(9, ultima_linha_dados)}",
                    FormulaRule(formula=[f'${letra}9="{v}"'], font=Font(bold=True, color=cor.upper())),
                )

    # --- total (SUBTOTAL: acompanha o filtro da planilha) e filtro
    fim_dados = max(9, ultima_linha_dados)
    if total:
        rt = fim_dados + 1 if ultima_linha_dados >= 9 else 10
        ws.row_dimensions[rt].height = 21.75
        lado = Side("medium", color=AZUL)
        for i, c in enumerate(colunas):
            cel = ws.cell(rt, i + 2)
            cel.font = _fonte(bold=True, color=AZUL)
            cel.fill = _preencher(TOTAL)
            cel.border = Border(top=lado)
            cel.alignment = Alignment(horizontal="right" if c.total else "left", vertical="center")
        ws.cell(rt, 2, "TOTAL")
        contador = ultima if not colunas[-1].total else (3 if n > 2 else 2)
        if contador != 2:
            ws.cell(rt, contador).value = f'=SUBTOTAL(103,B9:B{fim_dados})&" itens"'
            ws.cell(rt, contador).alignment = Alignment(horizontal="center", vertical="center")
        elif n == 1:
            ws.cell(rt, 2).value = f'=SUBTOTAL(103,B9:B{fim_dados})&" itens"'
        for i, c in enumerate(colunas):
            if c.total:
                letra = get_column_letter(i + 2)
                cel = ws.cell(rt, i + 2, f"=SUBTOTAL(109,{letra}9:{letra}{fim_dados})")
                cel.number_format = _TIPOS.get(c.tipo, _TIPOS["numero"])[0]
    ws.auto_filter.ref = f"B8:{get_column_letter(ultima)}{fim_dados}"
    ws.freeze_panes = "B9"

    # --- impressão: A4 paisagem, 1 página de largura, cabeçalho repetido, rodapé
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = 1.0
    ws.print_title_rows = "8:8"
    ws.oddFooter.left.text = "Grupo Transrota"
    ws.oddFooter.right.text = "Página &P de &N"
    ws.oddFooter.left.size = ws.oddFooter.right.size = 8

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _colunas_do_logo(colunas: list[Coluna]) -> int:
    """Quantas colunas, a partir da B, o logotipo (~196 px ≈ 27 caracteres) cobre."""
    soma = 0.0
    for i, c in enumerate(colunas):
        soma += c.largura
        if soma >= 27:
            return i + 1
    return len(colunas)


def resposta_xlsx(conteudo: bytes, nome: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(conteudo),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def hoje_brasilia() -> datetime.date:
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)).date()


# ----------------------------------------------------- rota para a tela ----

router = APIRouter()


def _nome_seguro(nome: str) -> str:
    nome = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(nome or "relatorio")).strip("._") or "relatorio"
    return nome if nome.lower().endswith(".xlsx") else nome + ".xlsx"


@router.post("/api/exportar-xlsx", dependencies=[Depends(require_auth_api)])
async def api_exportar_xlsx(request: Request):
    """Recebe o relatório que está na tela (linhas já filtradas, JSON, opcionalmente
    em gzip -- a Vercel limita o corpo a ~4,5 MB) e devolve o .xlsx no padrão Transrota."""
    corpo = await request.body()
    try:
        if request.headers.get("x-conteudo-gzip") == "1":
            corpo = gzip.decompress(corpo)
        dados = json.loads(corpo)
        colunas = [
            Coluna(
                titulo=str(c.get("t", ""))[:60],
                largura=min(max(float(c.get("w", 14)), 5), 70),
                tipo=c.get("tipo") if c.get("tipo") in _TIPOS else "texto",
                total=bool(c.get("total")),
                cores={str(k): str(v) for k, v in (c.get("cores") or {}).items()},
            )
            for c in dados["colunas"][:60]
        ]
        linhas = dados["linhas"]
        if not colunas or not isinstance(linhas, list):
            raise ValueError("relatório vazio")
    except Exception:
        return JSONResponse({"erro": "Não consegui ler os dados do relatório."}, status_code=400)
    if len(linhas) > MAX_LINHAS:
        return JSONResponse({"erro": f"Relatório com mais de {MAX_LINHAS} linhas: refine o filtro."}, status_code=413)
    info = [(str(a)[:30], str(b)[:120]) for a, b in (dados.get("info") or [])[:3]]
    if not any(r.startswith("Atualizado") for r, _ in info) and len(info) < 3:
        info.append(("Gerado em:", hoje_brasilia()))
    conteudo = criar_planilha(
        str(dados.get("titulo") or "Relatório")[:60],
        str(dados.get("subtitulo") or "Painel de Entregas")[:120],
        colunas, linhas, info=info, aba=str(dados.get("aba") or "Relatório"),
    )
    return resposta_xlsx(conteudo, _nome_seguro(dados.get("nome")))
