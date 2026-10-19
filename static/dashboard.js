(function () {
  "use strict";

  const STATUS_COLOR = {
    good: "var(--status-good)",
    warning: "var(--status-warning)",
    critical: "var(--status-critical)",
    serious: "var(--status-serious)"
  };
  const STATUS_LABEL = {
    good: "Entregue no prazo",
    warning: "Pendente (dentro do prazo)",
    critical: "Pendente (prazo vencido)",
    serious: "Entregue fora do prazo"
  };
  const STATUS_SHORT = {
    good: "No prazo",
    warning: "Pendente",
    critical: "Vencido",
    serious: "Atrasado"
  };

  function fmtDateLong(iso) {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
  }
  function fmtDateShort(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }
  function fmtN(n) { return n.toLocaleString("pt-BR"); }
  function fmtPct(n) { return (n * 100).toFixed(1).replace(".", ",") + "%"; }
  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

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

  async function loadData() {
    const resp = await fetch("/api/cargas", { credentials: "same-origin" });
    if (resp.status === 401) { window.location.href = "/login"; return null; }
    return resp.json();
  }

  function boot(DATA) {
    const records = DATA.records;
    const meta = DATA.meta;

    document.getElementById("header-sub").innerHTML =
      meta.total.toLocaleString("pt-BR") + " CT-e" +
      '<span class="dot">·</span>Atualizado em ' + fmtDateLong(meta.gerado_em) +
      '<span class="dot">·</span>' + esc(meta.fonte);

    const ufCounts = {};
    records.forEach(r => { ufCounts[r.uf_dest] = (ufCounts[r.uf_dest] || 0) + 1; });
    const ufSelect = document.getElementById("f-uf");
    ufSelect.querySelectorAll("option:not(:first-child)").forEach(o => o.remove());
    Object.keys(ufCounts).sort((a, b) => ufCounts[b] - ufCounts[a]).forEach(uf => {
      const opt = document.createElement("option");
      opt.value = uf; opt.textContent = uf + " (" + ufCounts[uf] + ")";
      ufSelect.appendChild(opt);
    });

    const state = {
      datePreset: "7",
      uf: "all",
      statuses: new Set(["good", "warning", "critical", "serious"]),
      search: "",
      onlyOpen: true,
      sortKey: "dias_atraso",
      sortDir: "desc",
      page: 1
    };

    const datePresetEl = document.getElementById("f-date-preset");
    const dateFromEl = document.getElementById("f-date-from");
    const dateToEl = document.getElementById("f-date-to");
    const dateSepEl = document.getElementById("f-date-sep");
    const REF_DATE = new Date(meta.referencia + "T00:00:00");
    // Sempre desconsidera a última semana de emissão: os CT-e dos últimos 7
    // dias ainda podem estar em rota, então distorcem os números. O corte vale
    // pra todos os períodos (inclusive "Todo o período" e "Personalizado"),
    // e os "Últimos N dias" contam pra trás a partir dele.
    const DIAS_EM_ROTA = 7;
    const DATA_CORTE = new Date(REF_DATE); DATA_CORTE.setDate(DATA_CORTE.getDate() - DIAS_EM_ROTA);
    const CORTE_ISO = DATA_CORTE.getFullYear() + "-" + String(DATA_CORTE.getMonth() + 1).padStart(2, "0") + "-" + String(DATA_CORTE.getDate()).padStart(2, "0");
    dateFromEl.max = CORTE_ISO; dateToEl.max = CORTE_ISO;
    const corteEl = document.getElementById("f-date-corte");
    if (corteEl) corteEl.textContent = "até " + DATA_CORTE.toLocaleDateString("pt-BR") + " (últimos " + DIAS_EM_ROTA + " dias não entram: podem estar em rota)";

    datePresetEl.addEventListener("change", function () {
      state.datePreset = this.value;
      const custom = this.value === "custom";
      dateFromEl.style.display = custom ? "inline-block" : "none";
      dateToEl.style.display = custom ? "inline-block" : "none";
      dateSepEl.style.display = custom ? "inline-block" : "none";
      render();
    });
    dateFromEl.addEventListener("change", render);
    dateToEl.addEventListener("change", render);
    ufSelect.addEventListener("change", function () { state.uf = this.value; render(); });
    document.querySelectorAll("#f-status .chip").forEach(chip => {
      chip.addEventListener("click", function () {
        const s = this.dataset.state;
        if (state.statuses.has(s)) { state.statuses.delete(s); this.classList.remove("active"); }
        else { state.statuses.add(s); this.classList.add("active"); }
        render();
      });
    });
    let searchDebounce;
    document.getElementById("f-search").addEventListener("input", function () {
      clearTimeout(searchDebounce);
      const v = this.value;
      searchDebounce = setTimeout(() => { state.search = v.trim().toLowerCase(); render(); }, 180);
    });
    document.getElementById("f-reset").addEventListener("click", function () {
      state.datePreset = "7"; state.uf = "all"; state.search = ""; state.onlyOpen = true;
      state.statuses = new Set(["good", "warning", "critical", "serious"]);
      datePresetEl.value = "7"; dateFromEl.style.display = "none"; dateToEl.style.display = "none"; dateSepEl.style.display = "none";
      ufSelect.value = "all"; document.getElementById("f-search").value = "";
      document.getElementById("t-only-open").checked = true;
      document.querySelectorAll("#f-status .chip").forEach(c => c.classList.add("active"));
      render();
    });
    document.getElementById("t-only-open").addEventListener("change", function () {
      state.onlyOpen = this.checked; state.page = 1; render();
    });
    document.getElementById("t-export").addEventListener("click", exportCsv);

    const syncBtn = document.getElementById("btn-sync");
    if (syncBtn) {
      syncBtn.addEventListener("click", async function () {
        syncBtn.disabled = true;
        const original = syncBtn.textContent;
        syncBtn.textContent = "Disparando…";
        try {
          const resp = await fetch("/api/sync/run", { method: "POST", credentials: "same-origin" });
          const body = await resp.json();
          if (!resp.ok || !body.ok) throw new Error(body.erro || "Falha ao disparar a sincronização.");
          alert("Sincronização disparada! Ela roda em segundo plano e leva 1-2 minutos — atualize a página depois para ver os dados novos.");
        } catch (e) {
          alert("Não foi possível disparar a sincronização: " + e.message);
        } finally {
          syncBtn.disabled = false;
          syncBtn.textContent = original;
        }
      });
    }

    function inDateRange(iso) {
      if (!iso) return state.datePreset === "all";
      const d = new Date(iso + "T00:00:00");
      if (d > DATA_CORTE) return false;  // última semana: ainda pode estar em rota
      if (state.datePreset === "all") return true;
      if (state.datePreset === "custom") {
        if (dateFromEl.value && d < new Date(dateFromEl.value + "T00:00:00")) return false;
        if (dateToEl.value && d > new Date(dateToEl.value + "T00:00:00")) return false;
        return true;
      }
      const days = parseInt(state.datePreset, 10);
      const inicio = new Date(DATA_CORTE); inicio.setDate(inicio.getDate() - days);
      return d >= inicio;
    }

    function getFiltered() {
      const q = state.search;
      return records.filter(r => {
        if (!state.statuses.has(r.estado)) return false;
        if (state.uf !== "all" && r.uf_dest !== state.uf) return false;
        if (!inDateRange(r.emissao)) return false;
        if (q && !(r.cte.toLowerCase().includes(q) || r.remetente.toLowerCase().includes(q) || r.destinatario.toLowerCase().includes(q))) return false;
        return true;
      });
    }

    function renderKpis(rows) {
      const total = rows.length;
      const pendentes = rows.filter(r => r.status === "PE").length;
      const vencidas = rows.filter(r => r.estado === "critical").length;
      const entreguesGood = rows.filter(r => r.status === "DP").length;
      const entreguesSerious = rows.filter(r => r.status === "FPE").length;
      const entreguesTotal = entreguesGood + entreguesSerious;
      const pctNoPrazo = entreguesTotal ? entreguesGood / entreguesTotal : 0;

      const tiles = [
        { k: "total", label: "Total de CT-e (filtro atual)", value: fmtN(total), sub: "de " + fmtN(meta.total) + " no relatório", cls: "" },
        { k: "pendentes", label: "Pendentes de entrega", value: fmtN(pendentes), sub: total ? fmtPct(pendentes / total) + " do total filtrado" : "—", cls: "warning" },
        { k: "vencidas", label: "Pendentes com prazo vencido", value: fmtN(vencidas), sub: pendentes ? fmtPct(vencidas / pendentes) + " das pendentes" : "—", cls: "critical" },
        { k: "no_prazo", label: "Entregues dentro do prazo", value: entreguesTotal ? fmtPct(pctNoPrazo) : "—", sub: fmtN(entreguesGood) + " de " + fmtN(entreguesTotal) + " entregues", cls: "good" },
        { k: "fora_prazo", label: "Entregues fora do prazo", value: fmtN(entreguesSerious), sub: entreguesTotal ? fmtPct(entreguesSerious / entreguesTotal) + " das entregas" : "—", cls: "serious" }
      ];
      // Clicar num card abre a tela com os CT-e dele (como na tela de Comprovantes).
      const el = document.getElementById("kpis");
      el.innerHTML = tiles.map(t =>
        '<button type="button" class="stat-tile ' + t.cls + '" data-grupo="' + t.k + '" title="Ver os CT-e">' +
        '<div class="label">' + esc(t.label) + '</div><div class="value">' + t.value + '</div><div class="sub">' + esc(t.sub) + '</div></button>'
      ).join("");
      el.querySelectorAll(".stat-tile").forEach(b => b.addEventListener("click", () => {
        location.hash = "lista/" + b.dataset.grupo;
      }));
    }

    // ---- tela "#lista/<grupo>": CT-e do card clicado, agrupados por
    // remetente (clientes recolhidos, CT-e e NF ao expandir). Respeita os
    // filtros do topo; o botão Voltar (ou o voltar do navegador) retorna ao
    // painel. Mesmo formato da tela de lista de Comprovantes.
    const GRUPOS = {
      total: { rotulo: "Total de CT-e", cor: "var(--text-muted)", filtro: () => true, desc: "todos os CT-e do filtro atual" },
      pendentes: { rotulo: "Pendentes de entrega", cor: "var(--status-warning)", filtro: r => r.status === "PE", desc: "carga ainda não entregue" },
      vencidas: { rotulo: "Pendentes com prazo vencido", cor: "var(--status-critical)", filtro: r => r.estado === "critical", desc: "não entregues e com o prazo vencido" },
      no_prazo: { rotulo: "Entregues dentro do prazo", cor: "var(--status-good)", filtro: r => r.status === "DP", desc: "entregues dentro do prazo" },
      fora_prazo: { rotulo: "Entregues fora do prazo", cor: "var(--status-serious)", filtro: r => r.status === "FPE", desc: "entregues depois do prazo" }
    };
    let vlBusca = "";
    let vlLinhas = [];
    const vlAbertos = new Set();  // remetentes abertos na tela de lista
    let vlAnterior = null;

    function vistaAberta() {
      const m = /^#lista\/(\w+)$/.exec(location.hash);
      if (!m || !GRUPOS[m[1]]) return null;
      return Object.assign({ chave: m[1] }, GRUPOS[m[1]]);
    }

    function rotear() {
      const v = vistaAberta();
      document.getElementById("vista-geral").hidden = !!v;
      document.getElementById("vista-lista").hidden = !v;
      hideTooltip();
      if (v) renderVista();
      window.scrollTo(0, 0);
    }

    function fmtDate(iso) {
      if (!iso) return "—";
      return new Date(iso + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
    }

    function resumoGrupo(rows) {
      const venc = rows.filter(r => r.estado === "critical").length;
      const pend = rows.filter(r => r.status === "PE").length;
      const fora = rows.filter(r => r.status === "FPE").length;
      const partes = [];
      if (venc) partes.push(fmtN(venc) + (venc === 1 ? " vencido" : " vencidos"));
      if (pend - venc > 0) partes.push(fmtN(pend - venc) + (pend - venc === 1 ? " pendente" : " pendentes"));
      if (fora) partes.push(fmtN(fora) + " fora do prazo");
      const atraso = rows.reduce((m, r) => Math.max(m, r.dias_atraso || 0), 0);
      if (atraso > 0) partes.push("maior atraso: " + fmtN(atraso) + (atraso === 1 ? " dia" : " dias"));
      if (!partes.length) partes.push("emissão mais antiga: " + fmtDate(rows.reduce((m, r) => (!m || (r.emissao && r.emissao < m) ? r.emissao : m), "")));
      return partes.join(" · ");
    }

    function renderVista() {
      const v = vistaAberta();
      if (!v) return;
      const recorte = getFiltered();

      document.getElementById("vl-titulo").innerHTML =
        '<span class="comp-vp-dot" style="background:' + v.cor + '"></span>' + esc(v.rotulo);
      document.getElementById("vl-abas").innerHTML = Object.keys(GRUPOS).map(k =>
        '<a href="#lista/' + k + '" class="comp-vp-aba' + (k === v.chave ? " ativa" : "") + '" style="--cor:' + GRUPOS[k].cor + '">' +
        '<span class="d"></span>' + esc(GRUPOS[k].rotulo) + ' <strong>' + fmtN(recorte.filter(GRUPOS[k].filtro).length) + '</strong></a>'
      ).join("");

      const q = vlBusca.trim().toLowerCase();
      const linhas = recorte.filter(r => v.filtro(r) && (!q ||
        (r.remetente || "").toLowerCase().includes(q) ||
        (r.destinatario || "").toLowerCase().includes(q) ||
        String(r.cte || "").toLowerCase().includes(q) ||
        String(r.notas_fiscais || "").toLowerCase().includes(q)))
        // Mais atrasados primeiro; depois os mais antigos.
        .sort((a, b) => (b.dias_atraso || 0) - (a.dias_atraso || 0) || String(a.emissao || "").localeCompare(String(b.emissao || "")));
      vlLinhas = linhas;
      const grupos = new Map();
      linhas.forEach(r => {
        const nome = r.remetente || "(sem remetente)";
        if (!grupos.has(nome)) grupos.set(nome, []);
        grupos.get(nome).push(r);
      });
      const ordem = Array.from(grupos.entries()).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "pt-BR"));

      document.getElementById("vl-sub").textContent =
        fmtN(linhas.length) + " CT-e de " + fmtN(ordem.length) + (ordem.length === 1 ? " cliente" : " clientes") + " · " + v.desc;

      const lista = document.getElementById("vl-lista");
      if (!linhas.length) {
        lista.innerHTML = '<div class="empty-state">Nenhum CT-e para os filtros atuais.</div>';
        return;
      }
      // Clientes vêm recolhidos -- a tabela de cada um só é montada quando
      // ele é aberto (pode haver milhares de CT-e). Com busca digitada, os
      // clientes encontrados já abrem.
      if (vlAnterior !== v.chave) { vlAbertos.clear(); vlAnterior = v.chave; }
      const comBusca = !!q;
      lista.innerHTML =
        '<div class="comp-vp-acoes"><span class="reset-link" data-acao="abrir">Expandir todos</span>' +
        '<span class="reset-link" data-acao="fechar">Recolher todos</span></div>' +
        ordem.map(([nome, rows], i) =>
          '<details class="comp-pend comp-vp-grupo" data-i="' + i + '" style="border-left-color:' + v.cor + '"' +
          (comBusca || vlAbertos.has(nome) ? " open" : "") + '>' +
          '<summary class="comp-pend-head"><span class="comp-vp-seta">▸</span><strong>' + esc(nome) + '</strong>' +
          '<span class="comp-vp-qtd" style="--cor:' + v.cor + '">' + fmtN(rows.length) + ' CT-e</span>' +
          '<span class="comp-de">' + esc(resumoGrupo(rows)) + '</span></summary>' +
          '<div class="comp-vp-corpo"></div></details>'
        ).join("");

      function preencher(det) {
        const corpo = det.querySelector(".comp-vp-corpo");
        if (corpo.dataset.ok) return;
        const rows = ordem[parseInt(det.dataset.i, 10)][1];
        corpo.innerHTML =
          '<div class="comp-pend-scroll comp-vp-scroll"><table class="detail comp-pend-table"><thead><tr>' +
          '<th>CT-e</th><th>Nota(s) fiscal(is)</th><th>Emissão</th><th>Destinatário</th><th>Cidade / UF</th><th>Entrega</th><th>Status</th><th>Dias</th><th>Última ocorrência</th><th>Manif.</th><th>Roman.</th>' +
          '</tr></thead><tbody>' + rows.map(r => {
            const diasStr = r.dias_atraso == null ? "—" : (r.dias_atraso > 0 ? "+" + r.dias_atraso : r.dias_atraso);
            return '<tr>' +
              '<td>' + esc(r.cte) + (r.serie ? '<span class="bip-muted">/' + esc(r.serie) + '</span>' : "") + '</td>' +
              '<td class="comp-pend-nf">' + esc(r.notas_fiscais || "—") + '</td>' +
              '<td>' + fmtDate(r.emissao) + '</td>' +
              '<td title="' + esc(r.destinatario) + '">' + esc(truncate(r.destinatario || "", 30)) + '</td>' +
              '<td>' + esc(r.cidade_dest) + '/' + esc(r.uf_dest) + '</td>' +
              '<td>' + fmtDate(r.data_baixa) + '</td>' +
              '<td><span class="status-pill" style="background:color-mix(in srgb, ' + STATUS_COLOR[r.estado] + ' 16%, transparent); color:' + STATUS_COLOR[r.estado] + ';"><span class="d" style="background:' + STATUS_COLOR[r.estado] + '"></span>' + esc(STATUS_SHORT[r.estado]) + '</span></td>' +
              '<td class="num">' + diasStr + '</td>' +
              '<td title="' + esc(r.ocorrencia || "") + '">' + esc(truncate(r.ocorrencia || "—", 30)) + '</td>' +
              '<td class="' + (r.manifesto ? "flag-yes" : "flag-no") + '">' + (r.manifesto ? "Sim" : "Não") + '</td>' +
              '<td class="' + (r.romaneio ? "flag-yes" : "flag-no") + '">' + (r.romaneio ? "Sim" : "Não") + '</td>' +
              '</tr>';
          }).join("") + '</tbody></table></div>';
        corpo.dataset.ok = "1";
      }

      lista.querySelectorAll("details.comp-vp-grupo").forEach(det => {
        if (det.open) preencher(det);
        det.addEventListener("toggle", () => {
          if (det.open) preencher(det);
          if (comBusca) return;  // aberto pela busca -- não fica aberto depois de limpar
          const nome = ordem[parseInt(det.dataset.i, 10)][0];
          if (det.open) vlAbertos.add(nome); else vlAbertos.delete(nome);
        });
      });
      lista.querySelectorAll(".comp-vp-acoes [data-acao]").forEach(b => b.addEventListener("click", () => {
        const abrir = b.dataset.acao === "abrir";
        lista.querySelectorAll("details.comp-vp-grupo").forEach(det => { det.open = abrir; });
      }));
    }

    document.getElementById("vl-voltar").addEventListener("click", e => {
      e.preventDefault();
      history.pushState("", document.title, location.pathname + location.search);
      rotear();
    });
    document.getElementById("vl-search").addEventListener("input", function () {
      vlBusca = this.value; renderVista();
    });
    document.getElementById("vl-export").addEventListener("click", () => {
      const v = vistaAberta();
      if (!v) return;
      const lines = [["Remetente", "CT-e", "Serie", "Notas fiscais", "Emissao", "Destinatario", "Cidade", "UF", "Entrega", "Status", "Dias", "Previsao", "Ocorrencia", "Manifesto", "Romaneio"].join(";")];
      vlLinhas.forEach(r => {
        lines.push([
          csvSafe(r.remetente || ""), r.cte, r.serie || "", csvSafe(r.notas_fiscais || ""), r.emissao || "", csvSafe(r.destinatario || ""),
          csvSafe(r.cidade_dest || ""), r.uf_dest || "", r.data_baixa || "", STATUS_SHORT[r.estado], r.dias_atraso == null ? "" : r.dias_atraso,
          r.previsao || "", csvSafe(r.ocorrencia || ""), r.manifesto ? "Sim" : "Nao", r.romaneio ? "Sim" : "Nao"
        ].join(";"));
      });
      const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "painel_" + v.chave + ".csv";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
    window.addEventListener("hashchange", rotear);

    function renderBarChart(containerId, items, opts) {
      opts = opts || {};
      const el = document.getElementById(containerId);
      el.innerHTML = "";
      if (!items.length) { el.innerHTML = '<div class="empty-state">Sem dados para o filtro atual.</div>'; return; }
      const max = Math.max.apply(null, items.map(d => d.value)) || 1;
      const color = opts.color || "var(--seq-450)";
      items.forEach(d => {
        const row = document.createElement("div");
        row.className = "bar-row";
        const pct = (d.value / max) * 100;
        row.innerHTML =
          '<div class="bar-label" title="' + esc(d.label) + '">' + esc(d.label) + '</div>' +
          '<div class="bar-track"><div class="bar-fill" style="width:' + pct.toFixed(1) + '%; background:' + color + ';"></div></div>' +
          '<div class="bar-value">' + fmtN(d.value) + '</div>';
        const fill = row.querySelector(".bar-fill");
        fill.addEventListener("pointerenter", e => showTooltip(e.clientX, e.clientY,
          '<div class="t-title">' + esc(d.label) + '</div><div class="t-row"><span>Pendências</span><span class="t-val">' + fmtN(d.value) + '</span></div>'));
        fill.addEventListener("pointermove", e => showTooltip(e.clientX, e.clientY,
          '<div class="t-title">' + esc(d.label) + '</div><div class="t-row"><span>Pendências</span><span class="t-val">' + fmtN(d.value) + '</span></div>'));
        fill.addEventListener("pointerleave", hideTooltip);
        el.appendChild(row);
      });
    }

    function topN(rows, keyFn, n) {
      const counts = new Map();
      rows.forEach(r => {
        const k = keyFn(r);
        if (k == null) return;
        counts.set(k, (counts.get(k) || 0) + 1);
      });
      return Array.from(counts.entries()).map(([label, value]) => ({ label, value }))
        .sort((a, b) => b.value - a.value).slice(0, n);
    }

    function renderStatusComposition(rows) {
      const order = ["good", "warning", "critical", "serious"];
      const total = rows.length;
      const counts = order.map(s => ({ s, n: rows.filter(r => r.estado === s).length }));
      const el = document.getElementById("chart-status");
      el.innerHTML = "";
      if (!total) { el.innerHTML = '<div class="empty-state">Sem dados para o filtro atual.</div>'; document.getElementById("legend-status").innerHTML = ""; return; }
      const wrap = document.createElement("div");
      wrap.style.cssText = "display:flex; height:28px; border-radius:6px; overflow:hidden; background:var(--gridline);";
      counts.forEach(c => {
        if (!c.n) return;
        const seg = document.createElement("div");
        const pct = (c.n / total) * 100;
        seg.style.cssText = "width:" + pct + "%; background:" + STATUS_COLOR[c.s] + "; position:relative; border-right:2px solid var(--surface-1);";
        seg.addEventListener("pointerenter", e => showTooltip(e.clientX, e.clientY,
          '<div class="t-title">' + esc(STATUS_LABEL[c.s]) + '</div><div class="t-row"><span>CT-e</span><span class="t-val">' + fmtN(c.n) + ' (' + fmtPct(c.n / total) + ')</span></div>'));
        seg.addEventListener("pointermove", e => showTooltip(e.clientX, e.clientY,
          '<div class="t-title">' + esc(STATUS_LABEL[c.s]) + '</div><div class="t-row"><span>CT-e</span><span class="t-val">' + fmtN(c.n) + ' (' + fmtPct(c.n / total) + ')</span></div>'));
        seg.addEventListener("pointerleave", hideTooltip);
        wrap.appendChild(seg);
      });
      el.appendChild(wrap);
      document.getElementById("legend-status").innerHTML = counts.map(c =>
        '<span class="legend-item"><span class="legend-swatch" style="background:' + STATUS_COLOR[c.s] + '"></span>' + esc(STATUS_LABEL[c.s]) + ' <span class="n">' + fmtN(c.n) + '</span> (' + fmtPct(total ? c.n / total : 0) + ')</span>'
      ).join("");
    }

    function renderTrend(rows) {
      const el = document.getElementById("chart-trend");
      el.innerHTML = "";
      if (!rows.length) { el.innerHTML = '<div class="empty-state">Sem dados para o filtro atual.</div>'; document.getElementById("legend-trend").innerHTML = ""; return; }
      const order = ["good", "warning", "critical", "serious"];
      const byDay = new Map();
      rows.forEach(r => {
        if (!byDay.has(r.emissao)) byDay.set(r.emissao, { good: 0, warning: 0, critical: 0, serious: 0 });
        byDay.get(r.emissao)[r.estado]++;
      });
      const days = Array.from(byDay.keys()).sort();
      const totals = days.map(d => order.reduce((s, k) => s + byDay.get(d)[k], 0));
      const maxTotal = Math.max.apply(null, totals) || 1;

      const W = 560, H = 190, padL = 34, padB = 34, padT = 8, padR = 8;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const barSlot = plotW / days.length;
      const barW = Math.min(24, barSlot * 0.62);

      const svgNS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("width", "100%");
      svg.setAttribute("height", H);
      svg.style.overflow = "visible";

      [0, 0.5, 1].forEach(f => {
        const y = padT + plotH * (1 - f);
        const line = document.createElementNS(svgNS, "line");
        line.setAttribute("x1", padL); line.setAttribute("x2", W - padR);
        line.setAttribute("y1", y); line.setAttribute("y2", y);
        line.setAttribute("class", "gridline");
        svg.appendChild(line);
        const label = document.createElementNS(svgNS, "text");
        label.setAttribute("x", padL - 6); label.setAttribute("y", y + 3);
        label.setAttribute("text-anchor", "end");
        label.textContent = Math.round(maxTotal * f);
        svg.appendChild(label);
      });
      const axis = document.createElementNS(svgNS, "line");
      axis.setAttribute("x1", padL); axis.setAttribute("x2", W - padR);
      axis.setAttribute("y1", padT + plotH); axis.setAttribute("y2", padT + plotH);
      axis.setAttribute("class", "axis-line");
      svg.appendChild(axis);

      days.forEach((day, i) => {
        const dayData = byDay.get(day);
        let yCursor = padT + plotH;
        const x = padL + i * barSlot + (barSlot - barW) / 2;
        order.forEach(k => {
          const v = dayData[k];
          if (!v) return;
          const segH = (v / maxTotal) * plotH;
          yCursor -= segH;
          const rect = document.createElementNS(svgNS, "rect");
          rect.setAttribute("x", x); rect.setAttribute("y", yCursor);
          rect.setAttribute("width", barW); rect.setAttribute("height", Math.max(0, segH - 1));
          rect.setAttribute("fill", STATUS_COLOR[k]);
          rect.setAttribute("rx", "1.5");
          rect.style.cursor = "pointer";
          rect.addEventListener("pointerenter", e => showTooltip(e.clientX, e.clientY, trendTooltip(day, dayData)));
          rect.addEventListener("pointermove", e => showTooltip(e.clientX, e.clientY, trendTooltip(day, dayData)));
          rect.addEventListener("pointerleave", hideTooltip);
          svg.appendChild(rect);
        });
        if (i % 2 === 0 || days.length <= 10) {
          const lbl = document.createElementNS(svgNS, "text");
          const lblX = x + barW / 2, lblY = H - padB + 16;
          lbl.setAttribute("x", lblX); lbl.setAttribute("y", lblY);
          lbl.setAttribute("text-anchor", "end");
          lbl.setAttribute("transform", `rotate(-45 ${lblX} ${lblY})`);
          lbl.textContent = fmtDateShort(day);
          svg.appendChild(lbl);
        }
      });
      el.appendChild(svg);
      document.getElementById("legend-trend").innerHTML = order.map(k =>
        '<span class="legend-item"><span class="legend-swatch" style="background:' + STATUS_COLOR[k] + '"></span>' + esc(STATUS_SHORT[k]) + '</span>'
      ).join("");
    }
    function trendTooltip(day, dayData) {
      const order = ["good", "warning", "critical", "serious"];
      const total = order.reduce((s, k) => s + dayData[k], 0);
      let rowsHtml = order.filter(k => dayData[k]).map(k =>
        '<div class="t-row"><span><span class="t-key" style="background:' + STATUS_COLOR[k] + '"></span> ' + esc(STATUS_SHORT[k]) + '</span><span class="t-val">' + fmtN(dayData[k]) + '</span></div>'
      ).join("");
      return '<div class="t-title">' + fmtDateLong(day) + ' · ' + fmtN(total) + ' CT-e</div>' + rowsHtml;
    }

    function renderCausas(rows) {
      const relevant = rows.filter(r => r.status !== "DP" && r.ocorrencia);
      const counts = new Map();
      relevant.forEach(r => counts.set(r.ocorrencia, (counts.get(r.ocorrencia) || 0) + 1));
      const sorted = Array.from(counts.entries()).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
      const top = sorted.slice(0, 8);
      const restSum = sorted.slice(8).reduce((s, d) => s + d.value, 0);
      if (restSum > 0) top.push({ label: "Outros motivos", value: restSum });
      renderBarChart("chart-causas", top, { color: "var(--seq-450)" });
    }

    function renderDoc(rows) {
      const pendentes = rows.filter(r => r.status === "PE");
      const el = document.getElementById("chart-doc");
      el.innerHTML = "";
      if (!pendentes.length) { el.innerHTML = '<div class="empty-state">Sem pendências no filtro atual.</div>'; return; }
      const semManifesto = pendentes.filter(r => !r.manifesto).length;
      const semRomaneio = pendentes.filter(r => !r.romaneio).length;
      [
        { label: "CT-e pendentes sem manifesto", n: semManifesto, total: pendentes.length },
        { label: "CT-e pendentes sem romaneio", n: semRomaneio, total: pendentes.length }
      ].forEach(m => {
        const pct = m.total ? m.n / m.total : 0;
        const div = document.createElement("div");
        div.className = "meter";
        div.innerHTML =
          '<div class="meter-head"><span>' + esc(m.label) + '</span><span class="m-val">' + fmtN(m.n) + ' (' + fmtPct(pct) + ')</span></div>' +
          '<div class="meter-track" style="background:var(--seq-100)"><div class="meter-fill" style="width:' + (pct * 100).toFixed(1) + '%; background:var(--status-critical);"></div></div>';
        el.appendChild(div);
      });
      const note = document.createElement("div");
      note.className = "chart-subtitle";
      note.style.marginTop = "10px";
      note.textContent = "Base: " + fmtN(pendentes.length) + " CT-e pendentes no filtro atual. Falta de manifesto ou romaneio costuma travar o processo antes mesmo da tentativa de entrega.";
      el.appendChild(note);
    }

    function renderTable(rows) {
      let data = state.onlyOpen ? rows.filter(r => r.status === "PE") : rows;
      document.getElementById("table-count").textContent = fmtN(data.length) + (state.onlyOpen ? " pendentes" : " registros");
      const key = state.sortKey, dir = state.sortDir === "asc" ? 1 : -1;
      data = data.slice().sort((a, b) => {
        let av = a[key], bv = b[key];
        if (av == null) av = ""; if (bv == null) bv = "";
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
        return String(av).localeCompare(String(bv), "pt-BR") * dir;
      });
      document.querySelectorAll("table.detail th").forEach(th => {
        th.querySelector(".arrow") && th.querySelector(".arrow").remove();
        if (th.dataset.key === state.sortKey) {
          const span = document.createElement("span");
          span.className = "arrow";
          span.textContent = state.sortDir === "asc" ? "▲" : "▼";
          th.appendChild(span);
        }
      });

      const perPage = 25;
      const totalPages = Math.max(1, Math.ceil(data.length / perPage));
      if (state.page > totalPages) state.page = totalPages;
      const start = (state.page - 1) * perPage;
      const pageRows = data.slice(start, start + perPage);

      const tbody = document.getElementById("table-body");
      if (!pageRows.length) {
        tbody.innerHTML = '<tr><td colspan="10"><div class="empty-state">Nenhum registro para os filtros atuais.</div></td></tr>';
      } else {
        tbody.innerHTML = pageRows.map(r => {
          const diasStr = r.dias_atraso == null ? "—" : (r.dias_atraso > 0 ? "+" + r.dias_atraso : r.dias_atraso);
          return '<tr>' +
            '<td>' + esc(r.cte) + '</td>' +
            '<td>' + fmtDateShort(r.emissao) + '</td>' +
            '<td title="' + esc(r.remetente) + '">' + esc(truncate(r.remetente, 26)) + '</td>' +
            '<td title="' + esc(r.destinatario) + '">' + esc(truncate(r.destinatario, 26)) + '</td>' +
            '<td>' + esc(r.cidade_dest) + '/' + esc(r.uf_dest) + '</td>' +
            '<td><span class="status-pill" style="background:color-mix(in srgb, ' + STATUS_COLOR[r.estado] + ' 16%, transparent); color:' + STATUS_COLOR[r.estado] + ';"><span class="d" style="background:' + STATUS_COLOR[r.estado] + '"></span>' + esc(STATUS_SHORT[r.estado]) + '</span></td>' +
            '<td class="num">' + diasStr + '</td>' +
            '<td title="' + esc(r.ocorrencia || "") + '">' + esc(truncate(r.ocorrencia || "—", 30)) + '</td>' +
            '<td class="' + (r.manifesto ? "flag-yes" : "flag-no") + '">' + (r.manifesto ? "Sim" : "Não") + '</td>' +
            '<td class="' + (r.romaneio ? "flag-yes" : "flag-no") + '">' + (r.romaneio ? "Sim" : "Não") + '</td>' +
            '</tr>';
        }).join("");
      }

      document.getElementById("pagination").innerHTML =
        '<button class="btn" id="p-prev" ' + (state.page <= 1 ? "disabled" : "") + '>Anterior</button>' +
        '<span>Página ' + state.page + ' de ' + totalPages + '</span>' +
        '<button class="btn" id="p-next" ' + (state.page >= totalPages ? "disabled" : "") + '>Próxima</button>';
      const prevBtn = document.getElementById("p-prev"), nextBtn = document.getElementById("p-next");
      if (prevBtn) prevBtn.addEventListener("click", () => { state.page--; renderTable(getFiltered()); });
      if (nextBtn) nextBtn.addEventListener("click", () => { state.page++; renderTable(getFiltered()); });

      window._currentTableData = data;
    }
    function truncate(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

    document.querySelectorAll("table.detail th").forEach(th => {
      th.addEventListener("click", function () {
        const key = this.dataset.key;
        if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        else { state.sortKey = key; state.sortDir = "desc"; }
        state.page = 1;
        renderTable(getFiltered());
      });
    });

    function exportCsv() {
      const rows = window._currentTableData || [];
      const headers = ["CT-e", "Emissao", "Remetente", "Destinatario", "Cidade", "UF", "Status", "Dias", "Ocorrencia", "Manifesto", "Romaneio"];
      const lines = [headers.join(";")];
      rows.forEach(r => {
        lines.push([
          r.cte, r.emissao, csvSafe(r.remetente), csvSafe(r.destinatario), csvSafe(r.cidade_dest), r.uf_dest,
          STATUS_SHORT[r.estado], r.dias_atraso == null ? "" : r.dias_atraso, csvSafe(r.ocorrencia || ""),
          r.manifesto ? "Sim" : "Nao", r.romaneio ? "Sim" : "Nao"
        ].join(";"));
      });
      const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "pendencias_filtrado.csv";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
    function csvSafe(s) { return '"' + String(s).replace(/"/g, '""') + '"'; }

    function render() {
      const rows = getFiltered();
      renderKpis(rows);
      renderStatusComposition(rows);
      renderTrend(rows);
      renderBarChart("chart-remetentes", topN(rows.filter(r => r.status === "PE"), r => r.remetente, 10), { color: "var(--seq-450)" });
      renderBarChart("chart-cidades", topN(rows.filter(r => r.status === "PE"), r => r.cidade_dest + "/" + r.uf_dest, 10), { color: "var(--seq-450)" });
      renderCausas(rows);
      renderDoc(rows);
      state.page = 1;
      renderTable(rows);
      if (vistaAberta()) renderVista();
    }

    render();
    rotear();
  }

  loadData().then(data => { if (data) boot(data); });
})();
