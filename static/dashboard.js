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
      datePreset: "all",
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
      state.datePreset = "all"; state.uf = "all"; state.search = ""; state.onlyOpen = true;
      state.statuses = new Set(["good", "warning", "critical", "serious"]);
      datePresetEl.value = "all"; dateFromEl.style.display = "none"; dateToEl.style.display = "none"; dateSepEl.style.display = "none";
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
        syncBtn.textContent = "Sincronizando…";
        try {
          const resp = await fetch("/api/sync/run", { method: "POST", credentials: "same-origin" });
          const body = await resp.json();
          if (!resp.ok || !body.ok) throw new Error(body.erro || "Falha na sincronização.");
          const fresh = await loadData();
          if (fresh) boot(fresh);
        } catch (e) {
          alert("Não foi possível sincronizar agora: " + e.message);
        } finally {
          syncBtn.disabled = false;
          syncBtn.textContent = original;
        }
      });
    }

    function inDateRange(iso) {
      if (state.datePreset === "all") return true;
      const d = new Date(iso + "T00:00:00");
      if (state.datePreset === "custom") {
        if (dateFromEl.value && d < new Date(dateFromEl.value + "T00:00:00")) return false;
        if (dateToEl.value && d > new Date(dateToEl.value + "T00:00:00")) return false;
        return true;
      }
      const days = parseInt(state.datePreset, 10);
      const cutoff = new Date(REF_DATE); cutoff.setDate(cutoff.getDate() - days);
      return d >= cutoff && d <= REF_DATE;
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
        { label: "Total de CT-e (filtro atual)", value: fmtN(total), sub: "de " + fmtN(meta.total) + " no relatório", cls: "" },
        { label: "Pendentes de entrega", value: fmtN(pendentes), sub: total ? fmtPct(pendentes / total) + " do total filtrado" : "—", cls: "warning" },
        { label: "Pendentes com prazo vencido", value: fmtN(vencidas), sub: pendentes ? fmtPct(vencidas / pendentes) + " das pendentes" : "—", cls: "critical" },
        { label: "Entregues dentro do prazo", value: entreguesTotal ? fmtPct(pctNoPrazo) : "—", sub: fmtN(entreguesGood) + " de " + fmtN(entreguesTotal) + " entregues", cls: "good" },
        { label: "Entregues fora do prazo", value: fmtN(entreguesSerious), sub: entreguesTotal ? fmtPct(entreguesSerious / entreguesTotal) + " das entregas" : "—", cls: "serious" }
      ];
      document.getElementById("kpis").innerHTML = tiles.map(t =>
        '<div class="stat-tile ' + t.cls + '"><div class="label">' + esc(t.label) + '</div><div class="value">' + t.value + '</div><div class="sub">' + esc(t.sub) + '</div></div>'
      ).join("");
    }

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

      const W = 560, H = 190, padL = 34, padB = 24, padT = 8, padR = 8;
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
          lbl.setAttribute("x", x + barW / 2); lbl.setAttribute("y", H - 6);
          lbl.setAttribute("text-anchor", "middle");
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
    }

    render();
  }

  loadData().then(data => { if (data) boot(data); });
})();
