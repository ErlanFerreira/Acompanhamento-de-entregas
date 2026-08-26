"""Coleta o relatório "Pendências" via automação de navegador (Playwright).

Por que isso existe: a API "GW Serviços" (ver sync_api.py) só retorna cargas
onde o CNPJ logado é parte (remetente/destinatário) -- um escopo de
"cliente", não de transportadora. O relatório personalizado "Pendências" no
portal Webtrans (gerado com o login/senha normal do usuário) traz a visão
completa da empresa, então esse módulo automatiza exatamente os cliques que
um usuário faria para gerar e baixar esse relatório.

Roda via Playwright (precisa de `playwright install chromium` previamente).
"""

import datetime
import logging

import requests
from playwright.sync_api import sync_playwright

from . import config

logger = logging.getLogger("scrape")

PORTAL_URL = "https://webtrans.saas.gwsistemas.com.br"
RELATORIO_URL = f"{PORTAL_URL}/relcartafrete.jsp?acao=iniciar&modulo=webtrans"
NOME_RELATORIO = "Pendências"


class ScrapeError(Exception):
    pass


def _fmt_data(d: datetime.date) -> str:
    return d.strftime("%d/%m/%Y")


def baixar_relatorio_pendencias(data_inicial: datetime.date, data_final: datetime.date) -> bytes:
    """Faz login no portal Webtrans, gera o relatório personalizado
    "Pendências" para o período informado e retorna os bytes do .xlsx."""
    if not config.PORTAL_EMAIL or not config.PORTAL_SENHA:
        raise ScrapeError("PORTAL_EMAIL e PORTAL_SENHA precisam estar configurados.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"{PORTAL_URL}/menu", wait_until="networkidle")

            # O login às vezes falha silenciosamente na primeira tentativa
            # (parece uma condição de corrida com scripts de terceiros da
            # página) -- tenta algumas vezes antes de desistir. Usa a URL
            # (não o título, que fica "Loading ..." transitoriamente) para
            # decidir se ainda está na tela de login.
            for tentativa in range(3):
                if "/login" not in page.url:
                    break
                page.wait_for_timeout(1500)
                page.fill('input[name="login"]', config.PORTAL_EMAIL)
                page.fill('input[name="senha"]', config.PORTAL_SENHA)
                page.click("button.button-login")
                page.wait_for_timeout(3000)
                page.wait_for_load_state("networkidle")

            if "/login" in page.url:
                raise ScrapeError("Login no portal GW Sistemas falhou -- verifique PORTAL_EMAIL/PORTAL_SENHA.")

            # Após o login, a SPA ainda redireciona/inicializa a sessão por
            # um instante -- navegar cedo demais para outra URL derruba a
            # sessão com erro 500.
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            page.goto(RELATORIO_URL, wait_until="networkidle")

            page.click("text=Relatórios Personalizados")
            page.wait_for_timeout(500)

            radio = page.locator("label", has_text=NOME_RELATORIO).locator("input[type=radio]")
            radio.click()
            page.wait_for_timeout(1500)  # AJAX carrega os filtros dinâmicos do relatório

            page.fill("#argumento_0", _fmt_data(data_inicial))
            page.fill("#input_0_ate", _fmt_data(data_final))
            page.check("#excel2")

            with page.expect_popup(timeout=30_000) as popup_info:
                page.evaluate("gerarRelatorio(true)")
            popup = popup_info.value
            popup.wait_for_selector("text=Clique no link", timeout=180_000)
            link = popup.eval_on_selector("a", "el => el.href")
            popup.close()

            if not link:
                raise ScrapeError("Não encontrei o link de download do relatório gerado.")
        finally:
            browser.close()

    resp = requests.get(link, timeout=180)
    resp.raise_for_status()
    return resp.content
