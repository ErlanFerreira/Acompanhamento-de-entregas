"""Resumo diário por e-mail: cada analista com e-mail cadastrado (tela
Usuários) recebe, nos dias úteis de manhã, as pendências das empresas dele --
quantos CT-e estão pendentes e vencidos, a variação desde a última foto do
Histórico, os mais atrasados e os tomadores com mais vencidos, com links que
abrem a lista no Painel.

Roda pelo GitHub Actions (.github/workflows/resumo_diario.yml) via
scripts/enviar_resumos.py. Variáveis de ambiente do envio: SMTP_HOST,
SMTP_PORT (465 = SSL, 587 = STARTTLS), SMTP_USUARIO e SMTP_SENHA (se faltarem,
usa IMAP_EMAIL / IMAP_SENHA -- a mesma conta de e-mail do bot) e APP_URL
(endereço do painel, usado nos links).
"""

import datetime
import html
import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import quote

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from . import escopo, historico
from .models import Carga, Usuario

logger = logging.getLogger("resumo_diario")

MAX_ATRASADOS = 15
MAX_TOMADORES = 8


def _app_url() -> str:
    return (os.environ.get("APP_URL") or "https://acompanhamento-de-entregas.vercel.app").rstrip("/")


def _fmt_data(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso


# --------------------------------------------------------------------- dados

def coletar(db: Session, usuario: Usuario) -> dict | None:
    """Números do analista, ou None se não há o que avisar (sem empresas, ou
    nenhuma pendência)."""
    try:
        empresas = {str(e).strip().upper() for e in json.loads(usuario.empresas or "[]") if str(e).strip()}
    except ValueError:
        empresas = set()
    if not empresas:
        return None

    base = db.query(Carga).filter(escopo.filtro_cargas(empresas))
    pendentes = base.filter(Carga.status == "PE").count()
    vencidos = base.filter(Carga.estado == "critical").count()
    if not pendentes and not vencidos:
        return None
    no_prazo = base.filter(Carga.status == "DP").count()
    fora = base.filter(Carga.status == "FPE").count()
    # pendentes separados pelo manifesto/romaneio: parados no depósito x em rota
    em_deposito = base.filter(historico.cond_em_deposito()).count()
    em_rota = base.filter(historico.cond_em_rota()).count()

    atrasados = (
        base.filter(Carga.estado == "critical")
        .order_by(func.coalesce(Carga.dias_atraso, 0).desc(), Carga.emissao.asc())
        .limit(MAX_ATRASADOS)
        .all()
    )
    maior_atraso = max([(c.dias_atraso or 0) for c in atrasados] or [0])

    nome = func.upper(func.trim(func.coalesce(Carga.consignatario, "")))
    por_tomador = (
        base.filter(Carga.status == "PE")
        .with_entities(nome, func.count(), func.sum(case((Carga.estado == "critical", 1), else_=0)))
        .group_by(nome)
        .all()
    )
    tomadores = []
    for n, pend, venc in por_tomador:
        tomadores.append({"tomador": n or historico.SEM_TOMADOR, "pendentes": int(pend), "vencidos": int(venc or 0)})
    tomadores.sort(key=lambda t: (-t["vencidos"], -t["pendentes"], t["tomador"]))

    # variação desde a foto anterior do Histórico (somando os tomadores dele)
    serie = historico.serie(db, 10, empresas)
    variacao = None
    if len(serie) >= 2:
        a, b = serie[-1], serie[-2]
        variacao = {
            "desde": b["data"], "pendentes": a["pendentes"] - b["pendentes"], "vencidos": a["vencidos"] - b["vencidos"],
        }

    entregues = no_prazo + fora
    return {
        "pendentes": pendentes, "vencidos": vencidos, "maior_atraso": maior_atraso,
        "em_deposito": em_deposito, "em_rota": em_rota,
        "pct_no_prazo": (no_prazo / entregues) if entregues else None, "entregues_fora": fora,
        "atrasados": [
            {
                "cte": f"{c.cte}/{c.serie}" if c.serie else str(c.cte or ""), "notas_fiscais": c.notas_fiscais or "",
                "tomador": c.consignatario or "", "destinatario": c.destinatario or "",
                "cidade": f"{c.cidade_dest or ''}/{c.uf_dest or ''}", "dias": c.dias_atraso or 0,
                "ocorrencia": c.ocorrencia or "", "emissao": c.emissao,
            }
            for c in atrasados
        ],
        "tomadores": tomadores[:MAX_TOMADORES],
        "variacao": variacao,
    }


# ------------------------------------------------------------------ conteúdo

def _sinal(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _texto_variacao(d: dict | None, chave: str) -> str:
    if not d:
        return ""
    v = d[chave]
    if v == 0:
        return f" (igual a {_fmt_data(d['desde'])})"
    return f" ({_sinal(v)} desde {_fmt_data(d['desde'])})"


def renderizar(usuario: Usuario, dados: dict, hoje: datetime.date) -> tuple[str, str, str]:
    """(assunto, texto simples, html)."""
    url = _app_url()
    link_venc = f"{url}/painel?periodo=all#lista/vencidas"
    link_pend = f"{url}/painel?periodo=all#lista/pendentes"
    primeiro_nome = (usuario.nome or "").split(" ")[0] or "olá"
    assunto = f"Entregas {hoje.strftime('%d/%m')}: {dados['vencidos']} vencidas, {dados['pendentes']} pendentes"
    var = dados["variacao"]

    # ---- texto simples
    linhas = [
        f"Bom dia, {primeiro_nome}!", "",
        f"Resumo das entregas das suas empresas em {hoje.strftime('%d/%m/%Y')} (CT-e do período sincronizado, até 90 dias):", "",
        f"- Pendentes de entrega: {dados['pendentes']}{_texto_variacao(var, 'pendentes')}",
        f"- Com prazo vencido: {dados['vencidos']}{_texto_variacao(var, 'vencidos')}",
    ]
    linhas.append(f"  - parados no depósito (sem manifesto nem romaneio): {dados['em_deposito']}")
    linhas.append(f"  - em rota de entrega (com manifesto ou romaneio): {dados['em_rota']}")
    if dados["maior_atraso"]:
        linhas.append(f"- Maior atraso: {dados['maior_atraso']} dia(s)")
    if dados["pct_no_prazo"] is not None:
        linhas.append(f"- Entregues no prazo: {dados['pct_no_prazo'] * 100:.1f}% ({dados['entregues_fora']} fora do prazo)".replace(".", ","))
    if dados["atrasados"]:
        linhas += ["", f"Mais atrasados ({len(dados['atrasados'])} de {dados['vencidos']}):"]
        for a in dados["atrasados"]:
            linhas.append(f"  {a['cte']} | {a['tomador']} -> {a['destinatario']} ({a['cidade']}) | +{a['dias']} dia(s) | NF {a['notas_fiscais'] or '—'}")
    if dados["tomadores"]:
        linhas += ["", "Tomadores com mais vencidos:"]
        for t in dados["tomadores"]:
            linhas.append(f"  {t['tomador']}: {t['vencidos']} vencidos, {t['pendentes']} pendentes")
    linhas += ["", f"Ver vencidas no Painel: {link_venc}", f"Ver todas as pendentes: {link_pend}", "",
               "Você recebe este resumo porque tem e-mail cadastrado na tela Usuários do Painel de Entregas."]
    texto = "\n".join(linhas)

    # ---- html
    e = html.escape
    def card(rotulo: str, valor: str, nota: str, cor: str) -> str:
        return (f'<td style="padding:12px 14px;border:1px solid #e3e6ea;border-left:5px solid {cor};border-radius:8px;">'
                f'<div style="font-size:12px;color:#5a6472;">{e(rotulo)}</div>'
                f'<div style="font-size:26px;font-weight:700;color:#1b2430;">{e(valor)}</div>'
                f'<div style="font-size:11px;color:#7a8594;">{e(nota)}</div></td>')

    cards = [
        card("Pendentes de entrega", str(dados["pendentes"]), _texto_variacao(var, "pendentes").strip(" ()") or "—", "#e0a100"),
        card("Prazo vencido", str(dados["vencidos"]), _texto_variacao(var, "vencidos").strip(" ()") or "—", "#c9352f"),
    ]
    if dados["pct_no_prazo"] is not None:
        cards.append(card("Entregues no prazo", f"{dados['pct_no_prazo'] * 100:.1f}%".replace(".", ","), f"{dados['entregues_fora']} fora do prazo", "#1a9d3a"))

    cards_logistica = [
        card("Parados no depósito", str(dados["em_deposito"]), "sem manifesto nem romaneio", "#eb6834"),
        card("Em rota de entrega", str(dados["em_rota"]), "com manifesto ou romaneio, até a baixa", "#2a78d6"),
    ]

    th = 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #e3e6ea;font-size:12px;color:#5a6472;"'
    td = 'style="padding:6px 8px;border-bottom:1px solid #eef0f3;font-size:13px;color:#1b2430;vertical-align:top;"'
    tabela_atrasados = ""
    if dados["atrasados"]:
        linhas_html = "".join(
            f'<tr><td {td}><strong>{e(a["cte"])}</strong></td><td {td}>{e(a["tomador"])}</td>'
            f'<td {td}>{e(a["destinatario"])}<br><span style="color:#7a8594;font-size:12px;">{e(a["cidade"])}</span></td>'
            f'<td {td} align="right"><strong style="color:#c9352f;">+{a["dias"]}</strong></td>'
            f'<td {td}>{e(a["notas_fiscais"] or "—")}</td></tr>'
            for a in dados["atrasados"]
        )
        tabela_atrasados = (
            f'<h3 style="margin:22px 0 8px;font-size:15px;">Mais atrasados ({len(dados["atrasados"])} de {dados["vencidos"]})</h3>'
            f'<table cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;"><tr>'
            f'<th {th}>CT-e</th><th {th}>Tomador</th><th {th}>Destinatário</th><th {th} align="right">Dias</th><th {th}>NF</th></tr>'
            f'{linhas_html}</table>'
        )
    tabela_tomadores = ""
    if dados["tomadores"]:
        linhas_t = "".join(
            f'<tr><td {td}><a href="{e(url)}/painel?periodo=all#lista/{"vencidas" if t["vencidos"] else "pendentes"}/tomador/{quote(t["tomador"])}" '
            f'style="color:#1d5fd1;text-decoration:none;">{e(t["tomador"])}</a></td>'
            f'<td {td} align="right">{t["vencidos"]}</td><td {td} align="right">{t["pendentes"]}</td></tr>'
            for t in dados["tomadores"]
        )
        tabela_tomadores = (
            f'<h3 style="margin:22px 0 8px;font-size:15px;">Tomadores com mais vencidos</h3>'
            f'<table cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;"><tr>'
            f'<th {th}>Tomador</th><th {th} align="right">Vencidos</th><th {th} align="right">Pendentes</th></tr>{linhas_t}</table>'
        )
    botao = ('display:inline-block;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;')
    corpo = (
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;margin:0 auto;color:#1b2430;">'
        f'<h2 style="margin:0 0 4px;font-size:20px;">Bom dia, {e(primeiro_nome)}!</h2>'
        f'<div style="color:#5a6472;font-size:13px;margin-bottom:16px;">Resumo das entregas das suas empresas em {hoje.strftime("%d/%m/%Y")}'
        ' · CT-e do período sincronizado (até 90 dias)</div>'
        f'<table cellspacing="8" cellpadding="0" style="width:100%;border-collapse:separate;"><tr>{"".join(cards)}</tr></table>'
        f'<table cellspacing="8" cellpadding="0" style="width:100%;border-collapse:separate;margin-top:-4px;"><tr>{"".join(cards_logistica)}</tr></table>'
        f'{tabela_atrasados}{tabela_tomadores}'
        f'<p style="margin:24px 0 8px;"><a href="{e(link_venc)}" style="{botao}background:#c9352f;color:#fff;">Ver vencidas no Painel</a> '
        f'<a href="{e(link_pend)}" style="{botao}background:#eef0f3;color:#1b2430;">Todas as pendentes</a></p>'
        '<p style="color:#7a8594;font-size:11px;">Você recebe este resumo porque tem e-mail cadastrado na tela Usuários do Painel de Entregas.</p>'
        '</div>'
    )
    return assunto, texto, corpo


# --------------------------------------------------------------------- envio

def _config_smtp() -> dict:
    host = os.environ.get("SMTP_HOST", "").strip()
    usuario = (os.environ.get("SMTP_USUARIO") or os.environ.get("IMAP_EMAIL") or "").strip()
    senha = os.environ.get("SMTP_SENHA") or os.environ.get("IMAP_SENHA") or ""
    if not (host and usuario and senha):
        raise RuntimeError(
            "Envio de e-mail não configurado: defina SMTP_HOST, SMTP_USUARIO e SMTP_SENHA "
            "(Secrets do GitHub) -- ou IMAP_EMAIL / IMAP_SENHA da caixa do SAC."
        )
    return {
        "host": host, "porta": int(os.environ.get("SMTP_PORT") or 465), "usuario": usuario, "senha": senha,
        "nome": os.environ.get("RESUMO_NOME_EXIBICAO") or "Painel de Entregas Transrota",
    }


def montar_mensagem(cfg: dict, destino: str, assunto: str, texto: str, corpo_html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr((cfg["nome"], cfg["usuario"]))
    msg["To"] = destino
    msg["Message-ID"] = make_msgid(domain=cfg["usuario"].split("@")[-1])
    msg.set_content(texto)
    msg.add_alternative(corpo_html, subtype="html")
    return msg


def _enviar(cfg: dict, mensagens: list[EmailMessage]) -> None:
    contexto = ssl.create_default_context()
    if cfg["porta"] == 465:
        servidor = smtplib.SMTP_SSL(cfg["host"], cfg["porta"], context=contexto, timeout=60)
    else:
        servidor = smtplib.SMTP(cfg["host"], cfg["porta"], timeout=60)
        servidor.starttls(context=contexto)
    with servidor:
        servidor.login(cfg["usuario"], cfg["senha"])
        for m in mensagens:
            servidor.send_message(m)


def executar(db: Session, login: str | None = None, dry_run: bool = False, saida: str | None = None) -> list[str]:
    """Envia o resumo a cada analista ativo com e-mail (ou só ao `login`).
    Com `dry_run` não envia: grava o HTML em `saida` e só relata. Devolve
    uma linha de relato por analista."""
    hoje = historico.hoje_brasilia()
    q = db.query(Usuario).filter(Usuario.ativo.is_(True), Usuario.email.isnot(None), Usuario.email != "")
    if login:
        q = q.filter(Usuario.login == login.strip().lower())
    usuarios = q.order_by(Usuario.nome).all()

    relato: list[str] = []
    prontos: list[tuple[Usuario, EmailMessage]] = []
    cfg = None if dry_run else (_config_smtp() if usuarios else None)

    for u in usuarios:
        dados = coletar(db, u)
        if dados is None:
            relato.append(f"{u.login}: nada a avisar (sem empresas ou sem pendências) -- não enviado.")
            continue
        assunto, texto, corpo = renderizar(u, dados, hoje)
        if dry_run:
            if saida:
                os.makedirs(saida, exist_ok=True)
                caminho = os.path.join(saida, f"resumo_{u.login}.html")
                with open(caminho, "w", encoding="utf-8") as f:
                    f.write(corpo)
            relato.append(f"{u.login} <{u.email}>: [simulação] {assunto}")
            continue
        prontos.append((u, montar_mensagem(cfg, u.email, assunto, texto, corpo)))
        relato.append(f"{u.login} <{u.email}>: {assunto}")

    if prontos:
        _enviar(cfg, [m for _, m in prontos])
    if not usuarios:
        relato.append("Nenhum analista ativo com e-mail cadastrado" + (f" para o login '{login}'." if login else "."))
    return relato
