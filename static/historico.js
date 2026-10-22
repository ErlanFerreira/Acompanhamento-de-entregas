(function () {
  "use strict";

  // Tela Histórico: tendências dia a dia (foto gravada a cada sincronização,
  // ver app/historico.py) e o ranking dos tomadores com mais vencidos, com a
  // variação contra uma data anterior.

  const COR = {
    pendentes: "var(--status-warning)",
    vencidos: "var(--status-critical)",
    prazo: "var(--status-good)"
  };

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function fmtN(n) { return Number(n || 0).toLocaleString("pt-BR"); }
  function fmtPct(n) { return (n * 100).toFixed(1).replace(".", ",") + "%"; }
  function fmtDia(iso) { return new Date(iso + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }); }
  function fmtDiaLongo(iso) { return new Date(iso + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" }); }

  const tooltip = document.getElementById("tooltip");
  function showTooltip(x, y, html) {
    tooltip.innerHTML = html;
    tooltip.style.display = "block";
    const rect = tooltip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + rect.width > window.innerWidth - 8) left = x - rect.width - 14;
    if (top + rect.height > window.innerHeight - 8) top = y - rect.height - 14;
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }
  function hideTooltip() { tooltip.style.display = "none"; }

  async function carregar(dias, comparar) {
    const resp = await fetch("/api/historico?dias=" + dias + "&comparar=" + comparar, { credentials: "same-origin" });
    if (resp.status === 401) { window.location.href = "/login"; return null; }
    return resp.json();
  }

  function pctNoPrazo(p) {
    const ent = p.entregues_no_prazo + p.entregues_fora;
    return ent ? p.entregues_no_prazo / ent : null;
  }

  // Variação em texto colorido: para pendentes/vencidos, subir é ruim.
  function variacao(agora, antes) {
    if (antes == null) return '<span class="bip-muted">—</span>';
    const d = agora - antes;
    if (!d) return '<span class="bip-muted">= 0</span>';
    const cor = d > 0 ? "var(--status-critical)" : "var(--status-good)";
    return '<span style="color:' + cor + '; font-weight:650;">' + (d > 0 ? "▲ +" : "▼ ") + fmtN(d) + '</span>';
  }

  function renderKpis(serie) {
    const el = document.getElementById("kpis");
    if (!serie.length) { el.innerHTML = ""; return; }
    const hoje = serie[serie.length - 1];
    const ontem = serie.length > 1 ? serie[serie.length - 2] : null;
    const pct = pctNoPrazo(hoje), pctAntes = ontem ? pctNoPrazo(ontem) : null;
    const dif = (a, b) => (b == null ? "sem dia anterior" : (a - b === 0 ? "igual ao dia anterior" : (a - b > 0 ? "+" : "") + fmtN(a - b) + " vs. dia anterior"));
    const tiles = [
      { label: "Pendentes de entrega", value: fmtN(hoje.pendentes), sub: dif(hoje.pendentes, ontem && ontem.pendentes), cls: "warning" },
      { label: "Pendentes com prazo vencido", value: fmtN(hoje.vencidos), sub: dif(hoje.vencidos, ontem && ontem.vencidos), cls: "critical" },
      { label: "Entregues dentro do prazo", value: pct == null ? "—" : fmtPct(pct),
        sub: pct == null || pctAntes == null ? "sem dia anterior" : ((pct - pctAntes) * 100 >= 0 ? "+" : "") + ((pct - pctAntes) * 100).toFixed(1).replace(".", ",") + " p.p. vs. dia anterior", cls: "good" },
      { label: "Entregues fora do prazo", value: fmtN(hoje.entregues_fora), sub: dif(hoje.entregues_fora, ontem && ontem.entregues_fora), cls: "serious" },
      { label: "Total de CT-e", value: fmtN(hoje.total), sub: "na janela sincronizada · " + fmtDiaLongo(hoje.data), cls: "" }
    ];
    el.innerHTML = tiles.map(t =>
      '<div class="stat-tile ' + t.cls + '"><div class="label">' + esc(t.label) + '</div><div class="value">' + t.value + '</div><div class="sub">' + esc(t.sub) + '</div></div>'
    ).join("");
  }

  // Gráfico de linhas em SVG: `linhas` = [{nome, cor, valores:[...]}] com um
  // valor por dia de `dias` (null = sem dado); `fmt` formata o valor.
  function desenharLinhas(containerId, dias, linhas, fmt, legendaId) {
    const el = document.getElementById(containerId);
    el.innerHTML = "";
    if (dias.length < 2) {
      el.innerHTML = '<div class="empty-state">' + (dias.length ? "Só há um dia gravado até agora -- o gráfico aparece a partir do segundo dia." : "Sem histórico ainda -- ele começa a ser gravado na próxima sincronização.") + '</div>';
      document.getElementById(legendaId).innerHTML = "";
      return;
    }
    const W = 560, H = 210, padL = 44, padB = 34, padT = 10, padR = 12;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const todos = linhas.flatMap(l => l.valores).filter(v => v != null);
    const maximo = Math.max.apply(null, todos.concat([1]));
    const topo = maximo * 1.08;
    const x = i => padL + (dias.length === 1 ? plotW / 2 : (i / (dias.length - 1)) * plotW);
    const y = v => padT + plotH * (1 - v / topo);

    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", H);
    svg.style.overflow = "visible";

    [0, 0.5, 1].forEach(f => {
      const yy = padT + plotH * (1 - f);
      const l = document.createElementNS(NS, "line");
      l.setAttribute("x1", padL); l.setAttribute("x2", W - padR); l.setAttribute("y1", yy); l.setAttribute("y2", yy);
      l.setAttribute("class", "gridline");
      svg.appendChild(l);
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", padL - 6); t.setAttribute("y", yy + 3); t.setAttribute("text-anchor", "end");
      t.textContent = fmt(topo * f);
      svg.appendChild(t);
    });

    const passo = Math.max(1, Math.ceil(dias.length / 8));
    dias.forEach((d, i) => {
      if (i % passo !== 0 && i !== dias.length - 1) return;
      const t = document.createElementNS(NS, "text");
      const tx = x(i), ty = H - padB + 16;
      t.setAttribute("x", tx); t.setAttribute("y", ty); t.setAttribute("text-anchor", "end");
      t.setAttribute("transform", "rotate(-45 " + tx + " " + ty + ")");
      t.textContent = fmtDia(d);
      svg.appendChild(t);
    });

    linhas.forEach(l => {
      const pontos = l.valores.map((v, i) => (v == null ? null : [x(i), y(v)]));
      const trecho = pontos.filter(Boolean).map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
      const pl = document.createElementNS(NS, "polyline");
      pl.setAttribute("points", trecho); pl.setAttribute("fill", "none"); pl.setAttribute("stroke-width", "2.2");
      pl.setAttribute("stroke-linejoin", "round"); pl.style.stroke = l.cor;
      svg.appendChild(pl);
      if (dias.length <= 45) {
        pontos.forEach(p => {
          if (!p) return;
          const c = document.createElementNS(NS, "circle");
          c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]); c.setAttribute("r", 2.6); c.style.fill = l.cor;
          svg.appendChild(c);
        });
      }
    });

    // faixa invisível por dia pra mostrar o tooltip com todas as linhas
    const larg = plotW / Math.max(dias.length - 1, 1);
    dias.forEach((d, i) => {
      const r = document.createElementNS(NS, "rect");
      r.setAttribute("x", x(i) - larg / 2); r.setAttribute("y", padT); r.setAttribute("width", larg); r.setAttribute("height", plotH);
      r.setAttribute("fill", "transparent");
      const mostrar = e => showTooltip(e.clientX, e.clientY,
        '<div class="t-title">' + fmtDiaLongo(d) + '</div>' + linhas.map(l =>
          '<div class="t-row"><span><span class="t-key" style="background:' + l.cor + '"></span> ' + esc(l.nome) + '</span><span class="t-val">' + (l.valores[i] == null ? "—" : fmt(l.valores[i])) + '</span></div>').join(""));
      r.addEventListener("pointerenter", mostrar);
      r.addEventListener("pointermove", mostrar);
      r.addEventListener("pointerleave", hideTooltip);
      svg.appendChild(r);
    });
    el.appendChild(svg);
    document.getElementById(legendaId).innerHTML = linhas.map(l =>
      '<span class="legend-item"><span class="legend-swatch" style="background:' + l.cor + '"></span>' + esc(l.nome) + '</span>').join("");
  }

  function renderRanking(rk) {
    const corpo = document.getElementById("rank-body");
    const sub = document.getElementById("rank-sub");
    if (!rk.data_atual) {
      sub.textContent = "";
      corpo.innerHTML = '<tr><td colspan="5"><div class="empty-state">Sem histórico ainda.</div></td></tr>';
      return;
    }
    sub.textContent = "Posição de " + fmtDiaLongo(rk.data_atual) + (rk.data_comparacao ? " · variação desde " + fmtDiaLongo(rk.data_comparacao) : " · ainda não há dia anterior pra comparar") +
      " · clique num tomador para ver os CT-e";
    if (!rk.itens.length) {
      corpo.innerHTML = '<tr><td colspan="5"><div class="empty-state">Nenhum tomador com pendências.</div></td></tr>';
      return;
    }
    corpo.innerHTML = rk.itens.map(i => {
      const href = "/painel?periodo=all#lista/" + (i.vencidos ? "vencidas" : "pendentes") + "/tomador/" + encodeURIComponent(i.tomador);
      return '<tr>' +
        '<td><a href="' + esc(href) + '" class="reset-link" title="Abrir os CT-e no Painel">' + esc(i.tomador) + '</a></td>' +
        '<td class="num">' + fmtN(i.pendentes) + '</td>' +
        '<td class="num">' + fmtN(i.vencidos) + '</td>' +
        '<td class="num">' + variacao(i.vencidos, i.vencidos_antes) + '</td>' +
        '<td class="num">' + variacao(i.pendentes, i.pendentes_antes) + '</td>' +
        '</tr>';
    }).join("");
  }

  async function atualizar() {
    const dias = parseInt(document.getElementById("f-dias").value, 10);
    const comparar = parseInt(document.getElementById("f-comparar").value, 10);
    const dados = await carregar(dias, comparar);
    if (!dados) return;
    const serie = dados.serie;

    document.getElementById("header-sub").textContent = serie.length
      ? fmtN(serie.length) + (serie.length === 1 ? " dia gravado" : " dias gravados") + " · de " + fmtDiaLongo(serie[0].data) + " a " + fmtDiaLongo(serie[serie.length - 1].data)
      : "Ainda sem dados gravados -- o histórico começa na próxima sincronização.";

    renderKpis(serie);
    const dias_ = serie.map(p => p.data);
    desenharLinhas("chart-pendentes", dias_, [
      { nome: "Pendentes", cor: COR.pendentes, valores: serie.map(p => p.pendentes) },
      { nome: "Vencidos", cor: COR.vencidos, valores: serie.map(p => p.vencidos) }
    ], v => fmtN(Math.round(v)), "legend-pendentes");
    desenharLinhas("chart-prazo", dias_, [
      { nome: "Entregues no prazo", cor: COR.prazo, valores: serie.map(p => { const v = pctNoPrazo(p); return v == null ? null : v * 100; }) }
    ], v => v.toFixed(1).replace(".", ",") + "%", "legend-prazo");
    renderRanking(dados.ranking);
  }

  document.getElementById("f-dias").addEventListener("change", atualizar);
  document.getElementById("f-comparar").addEventListener("change", atualizar);
  atualizar().catch(e => { document.getElementById("header-sub").textContent = "Erro ao carregar: " + e.message; });
})();
