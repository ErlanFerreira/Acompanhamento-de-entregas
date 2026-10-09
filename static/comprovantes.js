(function () {
  "use strict";

  // Situação do comprovante de cada CT-e -- "com" quando o GW tem "Data
  // Comprovante"; sem ela, separa o que já foi entregue (falta de verdade)
  // do que ainda nem chegou (o comprovante ainda não existe).
  // Verde/amarelo/vermelho/roxo ficam reservados pro alerta de prazo -- a
  // situação usa verde (recebido), azul e laranja.
  const ORDEM = ["com", "falta_entregue", "falta_pendente"];
  const COR = {
    com: "var(--status-good)",
    falta_entregue: "var(--series-1)",
    falta_pendente: "var(--series-2)"
  };
  const ROTULO = {
    com: "Com comprovante",
    falta_entregue: "Entregue sem comprovante",
    falta_pendente: "Ainda não entregue"
  };
  const CURTO = {
    com: "Com comprovante",
    falta_entregue: "Falta (entregue)",
    falta_pendente: "Não entregue"
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
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("pt-BR");
  }
  function fmtN(n) { return n.toLocaleString("pt-BR"); }
  function fmtPct(n) { return (n * 100).toFixed(1).replace(".", ",") + "%"; }
  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function truncate(s, n) { s = s || ""; return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function fmtCnpj(c) {
    c = String(c || "");
    return c.length === 14 ? c.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5") : c;
  }

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
  function bindTooltip(el, htmlFn) {
    el.addEventListener("pointerenter", e => showTooltip(e.clientX, e.clientY, htmlFn()));
    el.addEventListener("pointermove", e => showTooltip(e.clientX, e.clientY, htmlFn()));
    el.addEventListener("pointerleave", hideTooltip);
  }
  function tooltipContagem(titulo, contagem) {
    const total = ORDEM.reduce((s, k) => s + (contagem[k] || 0), 0);
    return '<div class="t-title">' + esc(titulo) + ' · ' + fmtN(total) + ' CT-e</div>' +
      ORDEM.filter(k => contagem[k]).map(k =>
        '<div class="t-row"><span><span class="t-key" style="background:' + COR[k] + '"></span> ' + esc(ROTULO[k]) + '</span><span class="t-val">' + fmtN(contagem[k]) + ' (' + fmtPct(contagem[k] / total) + ')</span></div>'
      ).join("");
  }
  function contar(rows) {
    const c = { com: 0, falta_entregue: 0, falta_pendente: 0 };
    rows.forEach(r => { c[r.situacao]++; });
    return c;
  }

  async function loadData() {
    const [resp, respTrat] = await Promise.all([
      fetch("/api/cargas", { credentials: "same-origin" }),
      fetch("/api/tratativas", { credentials: "same-origin" })
    ]);
    if (resp.status === 401) { window.location.href = "/login"; return null; }
    const data = await resp.json();
    data.tratativas = respTrat.ok ? (await respTrat.json()).tratativas : [];
    return data;
  }

  function situacaoDe(r) {
    if (r.data_comprovante) return "com";
    const entregue = r.entregue || !!r.data_baixa || r.status === "DP" || r.status === "FPE";
    return entregue ? "falta_entregue" : "falta_pendente";
  }

  // ---- alerta de prazo do comprovante ----
  // Prazo em dias úteis a partir da entrega, pela distância de Carpina até
  // o destino (calculado no servidor -- app/prazo_comprovante.py). Só vale
  // pros entregues sem comprovante.
  const ALERTAS = ["no_prazo", "alerta", "vencido", "critico"];
  const COR_ALERTA = {
    no_prazo: "var(--status-good)",
    alerta: "var(--status-warning)",
    vencido: "var(--status-critical)",
    critico: "var(--status-purple)"
  };
  const ROTULO_ALERTA = { no_prazo: "No prazo", alerta: "Alerta", vencido: "Vencido", critico: "Crítico" };
  // Gravidade pra ordenar: crítico primeiro; sem alerta (recebido, não
  // entregue, sem data de entrega) por último.
  const RANK_ALERTA = { critico: 4, vencido: 3, alerta: 2, no_prazo: 1 };
  const ALERTA_DIAS_ALERTA = 1;   // vence hoje ou no próximo dia útil
  const ALERTA_DIAS_CRITICO = 5;  // mais de 5 dias úteis vencido

  function isoUTC(iso) { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); }
  function utcIso(dt) { return dt.toISOString().slice(0, 10); }
  function somarDias(dt, n) { const r = new Date(dt); r.setUTCDate(r.getUTCDate() + n); return r; }

  // Feriados nacionais (inclui Carnaval, que pára o comercial na prática).
  const cacheFeriados = {};
  function feriados(ano) {
    if (cacheFeriados[ano]) return cacheFeriados[ano];
    // Páscoa (algoritmo de Meeus/Jones/Butcher).
    const a = ano % 19, b = Math.floor(ano / 100), c = ano % 100, d = Math.floor(b / 4), e = b % 4;
    const f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30;
    const i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
    const mes = Math.floor((h + l - 7 * m + 114) / 31), dia = ((h + l - 7 * m + 114) % 31) + 1;
    const pascoa = new Date(Date.UTC(ano, mes - 1, dia));
    const s = new Set(["01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-25"].map(md => ano + "-" + md));
    [-48, -47, -2, 60].forEach(n => s.add(utcIso(somarDias(pascoa, n))));
    cacheFeriados[ano] = s;
    return s;
  }
  function diaUtil(dt) {
    const dow = dt.getUTCDay();
    return dow !== 0 && dow !== 6 && !feriados(dt.getUTCFullYear()).has(utcIso(dt));
  }
  function somarUteis(dt, n) {
    let r = new Date(dt);
    while (n > 0) { r = somarDias(r, 1); if (diaUtil(r)) n--; }
    return r;
  }
  // Dias úteis depois de `de` até `ate` (inclusive), com `ate` > `de`.
  function uteisEntre(de, ate) {
    let n = 0;
    for (let r = somarDias(de, 1); r <= ate; r = somarDias(r, 1)) if (diaUtil(r)) n++;
    return n;
  }

  function calcularPrazo(r, hojeIso) {
    if (r.situacao !== "falta_entregue" || !r.data_baixa) return {};
    const hoje = isoUTC(hojeIso);
    const venc = somarUteis(isoUTC(r.data_baixa), r.prazo_comprovante_dias || 10);
    const out = { vencimento: utcIso(venc) };
    if (hoje <= venc) {
      out.restantes = uteisEntre(hoje, venc);
      out.alerta = out.restantes <= ALERTA_DIAS_ALERTA ? "alerta" : "no_prazo";
    } else {
      out.atraso = uteisEntre(venc, hoje);
      out.alerta = out.atraso > ALERTA_DIAS_CRITICO ? "critico" : "vencido";
    }
    return out;
  }

  function textoPrazo(r) {
    if (!r.alerta) return r.situacao === "falta_entregue" ? "Sem data de entrega no GW" : "";
    if (r.alerta === "no_prazo" || r.alerta === "alerta") {
      const quando = r.restantes === 0 ? "vence hoje" : r.restantes === 1 ? "vence no próximo dia útil" : "faltam " + r.restantes + " dias úteis";
      return quando + " (" + fmtDateShort(r.vencimento) + ")";
    }
    return r.atraso ? r.atraso + (r.atraso > 1 ? " dias úteis" : " dia útil") + " de atraso" : "venceu " + fmtDateShort(r.vencimento);
  }

  function tooltipPrazo(r) {
    const dist = r.distancia_km == null ? "mais de 200 km (ou cidade não localizada)" : r.distancia_km + " km";
    return '<div class="t-title">CT-e ' + esc(r.cte) + ' · ' + esc(ROTULO_ALERTA[r.alerta] || "Sem alerta") + '</div>' +
      '<div class="t-row"><span>Destino</span><span class="t-val">' + esc((r.cidade_dest || "—") + "/" + (r.uf_dest || "")) + '</span></div>' +
      '<div class="t-row"><span>Distância de Carpina</span><span class="t-val">' + esc(dist) + '</span></div>' +
      '<div class="t-row"><span>Prazo</span><span class="t-val">' + (r.prazo_comprovante_dias || 10) + ' dias úteis</span></div>' +
      '<div class="t-row"><span>Entrega</span><span class="t-val">' + fmtDate(r.data_baixa) + '</span></div>' +
      (r.vencimento ? '<div class="t-row"><span>Vencimento</span><span class="t-val">' + fmtDate(r.vencimento) + '</span></div>' : "");
  }

  function pillPrazo(r) {
    if (!r.alerta) return r.situacao === "falta_entregue" ? '<span class="bip-muted" title="Sem data de entrega no GW">—</span>' : "";
    const cor = COR_ALERTA[r.alerta];
    return '<span class="comp-prazo"><span class="status-pill" style="background:color-mix(in srgb, ' + cor + ' 18%, transparent); color:var(--text-primary);"><span class="d" style="background:' + cor + '"></span>' +
      esc(ROTULO_ALERTA[r.alerta]) + '</span><span class="comp-prazo-txt">' + esc(textoPrazo(r)) + '</span></span>';
  }
  function bindTooltipsPrazo(container, rowsByIdx) {
    container.querySelectorAll("[data-prazo]").forEach(el => {
      const r = rowsByIdx[parseInt(el.dataset.prazo, 10)];
      if (r && r.alerta) bindTooltip(el, () => tooltipPrazo(r));
    });
  }

  function boot(DATA) {
    const meta = DATA.meta;
    const records = DATA.records.map(r => {
      const rec = Object.assign({}, r, {
        situacao: situacaoDe(r),
        filial_nome: r.filial || fmtCnpj(r.cnpj_filial) || "—",
        consignatario: r.consignatario || "—",
        remetente: r.remetente || "",
        destinatario: r.destinatario || ""
      });
      Object.assign(rec, calcularPrazo(rec, meta.referencia));
      rec.alerta_rank = RANK_ALERTA[rec.alerta] || 0;
      return rec;
    });

    document.getElementById("header-sub").innerHTML =
      fmtN(meta.total) + " CT-e sincronizados" +
      '<span class="dot">·</span>Atualizado em ' + fmtDateLong(meta.gerado_em) +
      '<span class="dot">·</span>Comprovante = "Data Comprovante" preenchida no GW';

    function preencherSelect(el, chaveFn) {
      const counts = new Map();
      records.forEach(r => { const k = chaveFn(r); counts.set(k, (counts.get(k) || 0) + 1); });
      Array.from(counts.keys()).sort((a, b) => a.localeCompare(b, "pt-BR")).forEach(k => {
        const opt = document.createElement("option");
        opt.value = k; opt.textContent = k + " (" + fmtN(counts.get(k)) + ")";
        el.appendChild(opt);
      });
    }
    const tomadorEl = document.getElementById("f-tomador");
    const filialEl = document.getElementById("f-filial");
    preencherSelect(tomadorEl, r => r.consignatario);
    preencherSelect(filialEl, r => r.filial_nome);

    const TODAS = ["com", "falta_entregue", "falta_pendente"];
    const state = {
      datePreset: "all",
      tomador: "all",
      filial: "all",
      situacoes: new Set(TODAS),
      search: ""
    };

    const datePresetEl = document.getElementById("f-date-preset");
    const dateFromEl = document.getElementById("f-date-from");
    const dateToEl = document.getElementById("f-date-to");
    const dateSepEl = document.getElementById("f-date-sep");
    const REF_DATE = new Date(meta.referencia + "T00:00:00");

    datePresetEl.addEventListener("change", function () {
      state.datePreset = this.value;
      const custom = this.value === "custom";
      [dateFromEl, dateToEl, dateSepEl].forEach(el => { el.style.display = custom ? "inline-block" : "none"; });
      render();
    });
    dateFromEl.addEventListener("change", render);
    dateToEl.addEventListener("change", render);
    tomadorEl.addEventListener("change", function () { state.tomador = this.value; render(); });
    filialEl.addEventListener("change", function () { state.filial = this.value; render(); });
    document.querySelectorAll("#f-situacao .chip").forEach(chip => {
      chip.addEventListener("click", function () {
        const s = this.dataset.sit;
        if (state.situacoes.has(s)) { state.situacoes.delete(s); this.classList.remove("active"); }
        else { state.situacoes.add(s); this.classList.add("active"); }
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
      state.datePreset = "all"; state.tomador = "all"; state.filial = "all"; state.search = "";
      state.situacoes = new Set(TODAS);
      datePresetEl.value = "all";
      [dateFromEl, dateToEl, dateSepEl].forEach(el => { el.style.display = "none"; });
      tomadorEl.value = "all"; filialEl.value = "all";
      document.getElementById("f-search").value = "";
      document.querySelectorAll("#f-situacao .chip").forEach(c => c.classList.add("active"));
      render();
    });

    function inDateRange(iso) {
      if (state.datePreset === "all") return true;
      if (!iso) return false;
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

    // Filtros de "recorte" (período, tomador, filial, busca) valem pra tudo;
    // os chips de situação só escolhem o que aparece nos gráficos -- os
    // totais continuam mostrando o quadro inteiro.
    function getRecorte() {
      const q = state.search;
      return records.filter(r => {
        if (state.tomador !== "all" && r.consignatario !== state.tomador) return false;
        if (state.filial !== "all" && r.filial_nome !== state.filial) return false;
        if (!inDateRange(r.emissao)) return false;
        if (q && !(
          String(r.cte || "").toLowerCase().includes(q) ||
          String(r.notas_fiscais || "").toLowerCase().includes(q) ||
          r.consignatario.toLowerCase().includes(q) ||
          r.remetente.toLowerCase().includes(q) ||
          r.destinatario.toLowerCase().includes(q)
        )) return false;
        return true;
      });
    }

    function renderKpis(rows) {
      const total = rows.length;
      const c = contar(rows);
      const faltam = c.falta_entregue + c.falta_pendente;
      const entregues = c.com + c.falta_entregue;
      // Mesmo visual dos cards de prazo, nas cores da situação; "Faltam" é a
      // soma de entregues (azul) + não entregues (laranja), daí a faixa mista.
      const tiles = [
        { label: "Total de CT-e", value: fmtN(total), sub: "de " + fmtN(meta.total) + " sincronizados", cor: "var(--text-muted)" },
        { label: "Com comprovante", value: fmtN(c.com), sub: total ? fmtPct(c.com / total) + " do total" : "—", cor: COR.com },
        { label: "Faltam comprovante", value: fmtN(faltam), sub: total ? fmtPct(faltam / total) + " do total" : "—", cor: COR.falta_entregue, misto: true },
        { label: "Entregues sem comprovante", value: fmtN(c.falta_entregue), sub: entregues ? fmtPct(c.falta_entregue / entregues) + " das entregues · cobrar" : "—", cor: COR.falta_entregue },
        { label: "Ainda não entregues", value: fmtN(c.falta_pendente), sub: faltam ? fmtPct(c.falta_pendente / faltam) + " dos que faltam" : "—", cor: COR.falta_pendente }
      ];
      document.getElementById("kpis").innerHTML = tiles.map(t =>
        '<div class="comp-card' + (t.misto ? " misto" : "") + '" style="--cor:' + t.cor + '">' +
        '<div class="label"><span class="d"></span>' + esc(t.label) + '</div>' +
        '<div class="value">' + t.value + '</div><div class="sub">' + esc(t.sub) + '</div></div>'
      ).join("");
    }

    function contarAlertas(rows) {
      const c = { no_prazo: 0, alerta: 0, vencido: 0, critico: 0 };
      rows.forEach(r => { if (r.alerta) c[r.alerta]++; });
      return c;
    }

    // Quadro do prazo -- clicar num nível abre a tela com os CT-e dele.
    function renderPrazo(rows) {
      const c = contarAlertas(rows);
      const base = rows.filter(r => r.situacao === "falta_entregue").length;
      const semData = base - ALERTAS.reduce((s, k) => s + c[k], 0);
      const subs = {
        no_prazo: "mais de 1 dia útil pra vencer",
        alerta: "vence hoje ou no próximo dia útil",
        vencido: "até " + ALERTA_DIAS_CRITICO + " dias úteis de atraso",
        critico: "mais de " + ALERTA_DIAS_CRITICO + " dias úteis de atraso"
      };
      const el = document.getElementById("prazo-tiles");
      el.innerHTML = ALERTAS.map(k =>
        '<button type="button" class="comp-prazo-tile" data-alerta="' + k + '" style="--cor:' + COR_ALERTA[k] + '">' +
        '<div class="label"><span class="d"></span>' + esc(ROTULO_ALERTA[k]) + '</div>' +
        '<div class="value">' + fmtN(c[k]) + '</div>' +
        '<div class="sub">' + esc(subs[k]) + (base ? " · " + fmtPct(c[k] / base) : "") + '</div></button>'
      ).join("");
      el.querySelectorAll(".comp-prazo-tile").forEach(b => b.addEventListener("click", () => {
        location.hash = "prazo/" + b.dataset.alerta;
      }));
      document.getElementById("prazo-nota").textContent = semData
        ? fmtN(semData) + " CT-e entregue(s) sem comprovante não têm data de entrega no GW e ficam fora do alerta."
        : "";
    }

    function vazio(el) { el.innerHTML = '<div class="empty-state">Sem dados para o filtro atual.</div>'; }

    function renderComposicao(rows) {
      const el = document.getElementById("chart-composicao");
      const legend = document.getElementById("legend-composicao");
      el.innerHTML = "";
      const total = rows.length;
      if (!total) { vazio(el); legend.innerHTML = ""; return; }
      const c = contar(rows);
      const wrap = document.createElement("div");
      wrap.className = "comp-stack";
      ORDEM.forEach(k => {
        if (!c[k]) return;
        const seg = document.createElement("div");
        seg.style.width = (c[k] / total * 100) + "%";
        seg.style.background = COR[k];
        bindTooltip(seg, () => tooltipContagem(ROTULO[k], { [k]: c[k] }) +
          '<div class="t-row"><span>do total</span><span class="t-val">' + fmtPct(c[k] / total) + '</span></div>');
        wrap.appendChild(seg);
      });
      el.appendChild(wrap);
      legend.innerHTML = ORDEM.map(k =>
        '<span class="legend-item"><span class="legend-swatch" style="background:' + COR[k] + '"></span>' + esc(ROTULO[k]) + ' <span class="n">' + fmtN(c[k]) + '</span> (' + fmtPct(c[k] / total) + ')</span>'
      ).join("");
    }

    function inicioSemana(iso) {
      const d = new Date(iso + "T00:00:00");
      const dow = (d.getDay() + 6) % 7; // segunda = 0
      d.setDate(d.getDate() - dow);
      return d.toISOString().slice(0, 10);
    }

    function renderTrend(rows) {
      const el = document.getElementById("chart-trend");
      const legend = document.getElementById("legend-trend");
      el.innerHTML = "";
      const comData = rows.filter(r => r.emissao);
      if (!comData.length) { vazio(el); legend.innerHTML = ""; return; }

      // Mais de ~6 semanas por dia fica com barras finas demais -- agrupa por semana.
      const datas = comData.map(r => r.emissao).sort();
      const spanDias = (new Date(datas[datas.length - 1]) - new Date(datas[0])) / 86400000;
      const porSemana = spanDias > 45;
      document.getElementById("trend-subtitle").textContent = porSemana
        ? "CT-e emitidos por semana (início na segunda), pela situação do comprovante"
        : "CT-e emitidos por dia, pela situação do comprovante";

      const grupos = new Map();
      comData.forEach(r => {
        const k = porSemana ? inicioSemana(r.emissao) : r.emissao;
        if (!grupos.has(k)) grupos.set(k, { com: 0, falta_entregue: 0, falta_pendente: 0 });
        grupos.get(k)[r.situacao]++;
      });
      const chaves = Array.from(grupos.keys()).sort();
      const totais = chaves.map(k => ORDEM.reduce((s, o) => s + grupos.get(k)[o], 0));
      const maxTotal = Math.max.apply(null, totais) || 1;

      const W = 560, H = 190, padL = 34, padB = 34, padT = 8, padR = 8;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const slot = plotW / chaves.length;
      const barW = Math.min(24, slot * 0.62);
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

      const passoRotulo = Math.max(1, Math.ceil(chaves.length / 16));
      chaves.forEach((k, i) => {
        const g = grupos.get(k);
        const titulo = porSemana ? "Semana de " + fmtDateLong(k) : fmtDateLong(k);
        const x = padL + i * slot + (slot - barW) / 2;
        // Área de hover da coluna inteira (maior que a barra).
        const hit = document.createElementNS(svgNS, "rect");
        hit.setAttribute("x", padL + i * slot); hit.setAttribute("y", padT);
        hit.setAttribute("width", slot); hit.setAttribute("height", plotH);
        hit.setAttribute("fill", "transparent");
        bindTooltip(hit, () => tooltipContagem(titulo, g));
        svg.appendChild(hit);
        let y = padT + plotH;
        ORDEM.forEach(o => {
          if (!g[o]) return;
          const h = (g[o] / maxTotal) * plotH;
          y -= h;
          const rect = document.createElementNS(svgNS, "rect");
          rect.setAttribute("x", x); rect.setAttribute("y", y);
          rect.setAttribute("width", barW); rect.setAttribute("height", Math.max(0, h - 1));
          rect.setAttribute("fill", COR[o]);
          rect.setAttribute("rx", "1.5");
          rect.style.pointerEvents = "none";
          svg.appendChild(rect);
        });
        if (i % passoRotulo === 0) {
          const lbl = document.createElementNS(svgNS, "text");
          const lx = x + barW / 2, ly = H - padB + 16;
          lbl.setAttribute("x", lx); lbl.setAttribute("y", ly);
          lbl.setAttribute("text-anchor", "end");
          lbl.setAttribute("transform", "rotate(-45 " + lx + " " + ly + ")");
          lbl.textContent = fmtDateShort(k);
          svg.appendChild(lbl);
        }
      });
      el.appendChild(svg);
      legend.innerHTML = ORDEM.map(o =>
        '<span class="legend-item"><span class="legend-swatch" style="background:' + COR[o] + '"></span>' + esc(CURTO[o]) + '</span>'
      ).join("");
    }

    function renderFiliais(rows) {
      const el = document.getElementById("chart-filiais");
      el.innerHTML = "";
      const porFilial = new Map();
      rows.forEach(r => {
        if (!porFilial.has(r.filial_nome)) porFilial.set(r.filial_nome, { com: 0, falta_entregue: 0, falta_pendente: 0 });
        porFilial.get(r.filial_nome)[r.situacao]++;
      });
      if (!porFilial.size) { vazio(el); return; }
      Array.from(porFilial.entries())
        .map(([nome, c]) => ({ nome, c, total: c.com + c.falta_entregue + c.falta_pendente }))
        .sort((a, b) => b.total - a.total)
        .forEach(d => {
          const pct = d.c.com / d.total;
          const faltam = d.total - d.c.com;
          const div = document.createElement("div");
          div.className = "meter";
          div.innerHTML =
            '<div class="meter-head"><span>' + esc(d.nome) + '</span><span class="m-val">' + fmtPct(pct) +
            ' <span class="comp-de">· ' + fmtN(d.c.com) + ' de ' + fmtN(d.total) + ', faltam ' + fmtN(faltam) + '</span></span></div>' +
            '<div class="meter-track" style="background:var(--gridline)"><div class="meter-fill" style="width:' + (pct * 100).toFixed(1) + '%; background:var(--status-good);"></div></div>';
          bindTooltip(div.querySelector(".meter-track"), () => tooltipContagem(d.nome, d.c));
          el.appendChild(div);
        });
    }

    function baixarCsv(lines, nome) {
      const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = nome;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
    function csvSafe(s) { return '"' + String(s).replace(/"/g, '""') + '"'; }

    // ---- tratativas: o que está sendo feito pra conseguir cada comprovante ----
    // Lançadas pela janela de tratativa (botão na lista de CT-e de cada
    // cliente e no resultado da busca) e gravadas no banco (/api/tratativas).
    const ROTULO_TRAT = { andamento: "Em andamento", resolvido: "Resolvido" };
    const COR_TRAT = { andamento: "var(--series-1)", resolvido: "var(--status-good)" };
    const tratPorCte = new Map();  // id_cte -> lançamentos, do mais antigo pro mais novo
    (DATA.tratativas || []).forEach(t => {
      if (!tratPorCte.has(t.id_cte)) tratPorCte.set(t.id_cte, []);
      tratPorCte.get(t.id_cte).push(t);
    });
    const recPorId = new Map(records.map(r => [r.id_cte, r]));

    function ultimaTrat(idCte) {
      const lista = tratPorCte.get(idCte);
      return lista && lista.length ? lista[lista.length - 1] : null;
    }
    function fmtDataHora(iso) {
      if (!iso) return "—";
      return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    function pillTrat(situacao) {
      const cor = COR_TRAT[situacao] || "var(--text-muted)";
      return '<span class="status-pill" style="background:color-mix(in srgb, ' + cor + ' 18%, transparent); color:var(--text-primary);"><span class="d" style="background:' + cor + '"></span>' +
        esc(ROTULO_TRAT[situacao] || situacao) + '</span>';
    }
    // Célula "Tratativa" das listas -- `idx` aponta pra linha no array da tabela.
    function celulaTrat(r, idx) {
      const ult = ultimaTrat(r.id_cte);
      const n = (tratPorCte.get(r.id_cte) || []).length;
      if (!ult) return '<button type="button" class="btn comp-trat-btn" data-trat="' + idx + '">+ Registrar</button>';
      return '<div class="comp-trat-cel">' + pillTrat(ult.situacao) +
        '<span class="comp-trat-txt" title="' + esc(ult.texto) + '">' + esc(truncate(ult.texto, 40)) + '</span>' +
        '<button type="button" class="btn comp-trat-btn" data-trat="' + idx + '">' + (n > 1 ? "Ver (" + n + ")" : "Ver") + '</button></div>';
    }
    function bindBotoesTrat(container, rowsByIdx) {
      container.querySelectorAll("[data-trat]").forEach(b => b.addEventListener("click", e => {
        e.stopPropagation();
        abrirTratativa(rowsByIdx[parseInt(b.dataset.trat, 10)]);
      }));
    }
    function contarTrat(rows) {
      const c = { andamento: 0, resolvido: 0 };
      rows.forEach(r => { const u = ultimaTrat(r.id_cte); if (u) c[u.situacao] = (c[u.situacao] || 0) + 1; });
      return c;
    }

    // Janela de tratativa de um CT-e: histórico + formulário de novo lançamento.
    const modal = document.getElementById("trat-modal");
    let tratAtual = null;
    const RESP_KEY = "comprovantes-responsavel";

    function abrirTratativa(r) {
      tratAtual = r;
      renderModal();
      modal.hidden = false;
      const resp = document.getElementById("trat-responsavel");
      try { if (!resp.value) resp.value = localStorage.getItem(RESP_KEY) || ""; } catch (e) { /* sem storage */ }
      document.getElementById("trat-texto").value = "";
      document.getElementById("trat-erro").textContent = "";
      document.querySelector('input[name="trat-situacao"][value="andamento"]').checked = true;
      setTimeout(() => document.getElementById("trat-texto").focus(), 0);
    }
    function fecharTratativa() { modal.hidden = true; tratAtual = null; }

    function renderModal() {
      const r = tratAtual;
      document.getElementById("trat-titulo").innerHTML =
        'CT-e ' + esc(r.cte) + (r.serie ? '<span class="bip-muted">/' + esc(r.serie) + '</span>' : "") +
        ' <span class="comp-de">· ' + esc(r.consignatario || r.cliente || "—") + '</span>';
      const info = [
        ["Nota(s) fiscal(is)", esc(r.notas_fiscais || "—")],
        ["Destino", r.cidade_dest ? esc(r.cidade_dest + "/" + r.uf_dest) : "—"],
        ["Entrega", fmtDate(r.data_baixa)],
        ["Comprovante", r.data_comprovante ? "Recebido em " + fmtDate(r.data_comprovante) : (r.foraDaJanela ? "—" : "Falta")],
        ["Prazo", r.alerta ? pillPrazo(r) : "—"]
      ];
      document.getElementById("trat-info").innerHTML = info.map(([k, v]) =>
        '<div><span class="comp-trat-k">' + esc(k) + '</span><span>' + v + '</span></div>').join("") +
        (r.foraDaJanela ? '<div class="comp-trat-aviso">Este CT-e saiu da janela sincronizada (90 dias) -- dá pra consultar o histórico, mas não lançar tratativa nova.</div>' : "");

      const hist = tratPorCte.get(r.id_cte) || [];
      document.getElementById("trat-historico").innerHTML = hist.length
        ? hist.slice().reverse().map(t =>
            '<div class="comp-trat-item">' +
            '<div class="comp-trat-item-head">' + pillTrat(t.situacao) +
            '<span class="comp-de">' + fmtDataHora(t.criado_em) + (t.responsavel ? " · " + esc(t.responsavel) : "") + '</span>' +
            '<span class="reset-link comp-trat-apagar" data-id="' + t.id + '">apagar</span></div>' +
            '<div class="comp-trat-item-txt">' + esc(t.texto) + '</div></div>'
          ).join("")
        : '<div class="comp-de">Nenhuma tratativa registrada ainda.</div>';
      document.querySelectorAll(".comp-trat-apagar").forEach(a => a.addEventListener("click", () => apagarTratativa(parseInt(a.dataset.id, 10))));
      document.getElementById("trat-form").hidden = !!r.foraDaJanela;
    }

    async function salvarTratativa(e) {
      e.preventDefault();
      const erro = document.getElementById("trat-erro");
      const btn = document.getElementById("trat-salvar");
      const texto = document.getElementById("trat-texto").value.trim();
      const responsavel = document.getElementById("trat-responsavel").value.trim();
      const situacao = document.querySelector('input[name="trat-situacao"]:checked').value;
      if (!texto) { erro.textContent = "Descreva o que está sendo feito."; return; }
      btn.disabled = true; erro.textContent = "";
      try {
        const resp = await fetch("/api/tratativas", {
          method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id_cte: tratAtual.id_cte, texto, situacao, responsavel })
        });
        const body = await resp.json();
        if (!resp.ok) throw new Error(body.erro || "Falha ao salvar.");
        if (!tratPorCte.has(body.id_cte)) tratPorCte.set(body.id_cte, []);
        tratPorCte.get(body.id_cte).push(body);
        try { localStorage.setItem(RESP_KEY, responsavel); } catch (err) { /* sem storage */ }
        fecharTratativa();
        atualizarListas();
      } catch (err) {
        erro.textContent = err.message;
      } finally {
        btn.disabled = false;
      }
    }

    async function apagarTratativa(id) {
      if (!confirm("Apagar este lançamento de tratativa?")) return;
      const resp = await fetch("/api/tratativas/" + id, { method: "DELETE", credentials: "same-origin" });
      if (!resp.ok) { alert("Não foi possível apagar."); return; }
      const lista = tratPorCte.get(tratAtual.id_cte) || [];
      const i = lista.findIndex(t => t.id === id);
      if (i >= 0) lista.splice(i, 1);
      renderModal();
      atualizarListas();
    }

    document.getElementById("trat-form").addEventListener("submit", salvarTratativa);
    document.getElementById("trat-cancelar").addEventListener("click", fecharTratativa);
    document.getElementById("trat-fechar").addEventListener("click", fecharTratativa);
    modal.addEventListener("click", e => { if (e.target === modal) fecharTratativa(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !modal.hidden) fecharTratativa(); });

    // ---- busca por CT-e / NF (campo de busca do topo) ----
    // Procura em todos os CT-e sincronizados (sem os outros filtros) e no
    // texto das tratativas; tratativa de CT-e que já saiu da janela aparece
    // pelo que foi gravado no lançamento.
    const MAX_BUSCA = 100;
    function buscar(q) {
      const achados = records.filter(r =>
        String(r.cte || "").toLowerCase().includes(q) ||
        String(r.notas_fiscais || "").toLowerCase().includes(q) ||
        r.consignatario.toLowerCase().includes(q) ||
        r.destinatario.toLowerCase().includes(q) ||
        (tratPorCte.get(r.id_cte) || []).some(t => t.texto.toLowerCase().includes(q)));
      tratPorCte.forEach((lista, idCte) => {
        if (recPorId.has(idCte) || !lista.length) return;
        const t = lista[lista.length - 1];
        if (lista.some(x => String(x.cte || "").toLowerCase().includes(q) || String(x.notas_fiscais || "").toLowerCase().includes(q) ||
            String(x.cliente || "").toLowerCase().includes(q) || x.texto.toLowerCase().includes(q))) {
          achados.push({ id_cte: idCte, cte: t.cte, serie: t.serie, notas_fiscais: t.notas_fiscais, consignatario: t.cliente || "—", foraDaJanela: true });
        }
      });
      // Número exato de CT-e/NF primeiro, depois os mais recentes.
      const exato = r => String(r.cte || "") === q || String(r.notas_fiscais || "").split(/[^0-9]+/).includes(q) ? 0 : 1;
      return achados.sort((a, b) => exato(a) - exato(b) || String(b.emissao || "").localeCompare(String(a.emissao || "")));
    }

    function renderBusca() {
      const sec = document.getElementById("busca-sec");
      const q = state.search;
      if (!q || nivelAberto()) { sec.hidden = true; return; }
      sec.hidden = false;
      const achados = buscar(q);
      const mostrar = achados.slice(0, MAX_BUSCA);
      document.getElementById("busca-titulo").textContent = 'Resultado da busca por "' + q + '"';
      document.getElementById("busca-sub").textContent = achados.length
        ? fmtN(achados.length) + " CT-e encontrado(s)" + (achados.length > MAX_BUSCA ? " · mostrando os " + MAX_BUSCA + " primeiros, refine a busca" : "")
        : "Nenhum CT-e encontrado.";
      const corpo = document.getElementById("busca-corpo");
      if (!mostrar.length) { corpo.innerHTML = ""; return; }
      corpo.innerHTML =
        '<div class="comp-pend-scroll comp-busca-scroll"><table class="detail comp-pend-table"><thead><tr>' +
        '<th>CT-e</th><th>Nota(s) fiscal(is)</th><th>Cliente</th><th>Cidade / UF</th><th>Entrega</th><th>Comprovante</th><th>Prazo</th><th>Tratativa</th>' +
        '</tr></thead><tbody>' + mostrar.map((r, idx) =>
          '<tr>' +
          '<td>' + esc(r.cte) + (r.serie ? '<span class="bip-muted">/' + esc(r.serie) + '</span>' : "") + '</td>' +
          '<td class="comp-pend-nf">' + esc(r.notas_fiscais || "—") + '</td>' +
          '<td title="' + esc(r.consignatario) + '">' + esc(truncate(r.consignatario, 28)) + '</td>' +
          '<td>' + (r.foraDaJanela ? '<span class="bip-muted">fora da janela sincronizada</span>' : esc(r.cidade_dest) + '/' + esc(r.uf_dest)) + '</td>' +
          '<td>' + fmtDate(r.data_baixa) + '</td>' +
          '<td>' + (r.foraDaJanela ? "—" : r.data_comprovante ? fmtDate(r.data_comprovante) : '<span class="flag-no">Falta</span>') + '</td>' +
          '<td data-prazo="' + idx + '">' + (pillPrazo(r) || '<span class="bip-muted">—</span>') + '</td>' +
          '<td>' + celulaTrat(r, idx) + '</td>' +
          '</tr>'
        ).join("") + '</tbody></table></div>';
      bindTooltipsPrazo(corpo, mostrar);
      bindBotoesTrat(corpo, mostrar);
    }

    // Depois de lançar/apagar uma tratativa: atualiza a busca e a tela do
    // nível (mantendo abertos os clientes que estavam abertos).
    function atualizarListas() {
      renderBusca();
      if (nivelAberto()) renderVistaPrazo();
    }

    // ---- tela "#prazo/<nível>": CT-e e NF daquele nível, por cliente ----
    // Respeita os filtros do topo (período, tomador, filial, busca); o
    // botão Voltar (ou o voltar do navegador) retorna à visão geral.
    const DESCRICAO_ALERTA = {
      no_prazo: "falta mais de 1 dia útil para vencer",
      alerta: "vence hoje ou no próximo dia útil",
      vencido: "até " + ALERTA_DIAS_CRITICO + " dias úteis de atraso",
      critico: "mais de " + ALERTA_DIAS_CRITICO + " dias úteis de atraso"
    };
    let vpBusca = "";
    let vpLinhas = [];
    const vpAbertos = new Set();  // clientes abertos na tela do nível
    let vpNivelAnterior = null;

    function nivelAberto() {
      const m = /^#prazo\/(\w+)$/.exec(location.hash);
      return m && ALERTAS.includes(m[1]) ? m[1] : null;
    }

    function rotear() {
      const nivel = nivelAberto();
      document.getElementById("vista-geral").hidden = !!nivel;
      document.getElementById("vista-prazo").hidden = !nivel;
      document.getElementById("f-situacao").hidden = !!nivel;  // só faz sentido na visão geral
      hideTooltip();
      renderBusca();
      if (nivel) renderVistaPrazo();
      window.scrollTo(0, 0);
    }

    function linhasDoNivel(nivel) {
      const q = vpBusca.trim().toLowerCase();
      return getRecorte().filter(r => r.alerta === nivel && (!q ||
        r.consignatario.toLowerCase().includes(q) ||
        String(r.cte || "").toLowerCase().includes(q) ||
        String(r.notas_fiscais || "").toLowerCase().includes(q)));
    }

    function renderVistaPrazo() {
      const nivel = nivelAberto();
      if (!nivel) return;
      const recorte = getRecorte();
      const c = contarAlertas(recorte);
      const cor = COR_ALERTA[nivel];

      document.getElementById("vp-titulo").innerHTML =
        '<span class="comp-vp-dot" style="background:' + cor + '"></span>Prazo do comprovante: ' + esc(ROTULO_ALERTA[nivel]);
      document.getElementById("vp-abas").innerHTML = ALERTAS.map(k =>
        '<a href="#prazo/' + k + '" class="comp-vp-aba' + (k === nivel ? " ativa" : "") + '" style="--cor:' + COR_ALERTA[k] + '">' +
        '<span class="d"></span>' + esc(ROTULO_ALERTA[k]) + ' <strong>' + fmtN(c[k]) + '</strong></a>'
      ).join("");

      // Mais graves primeiro: maior atraso / vencimento mais antigo.
      const linhas = linhasDoNivel(nivel).sort((a, b) =>
        (b.atraso || 0) - (a.atraso || 0) || String(a.vencimento || "").localeCompare(String(b.vencimento || "")));
      vpLinhas = linhas;
      const grupos = new Map();
      linhas.forEach(r => {
        if (!grupos.has(r.consignatario)) grupos.set(r.consignatario, []);
        grupos.get(r.consignatario).push(r);
      });
      const ordem = Array.from(grupos.entries()).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "pt-BR"));

      document.getElementById("vp-sub").textContent =
        fmtN(linhas.length) + " CT-e de " + fmtN(ordem.length) + (ordem.length === 1 ? " cliente" : " clientes") +
        " · " + DESCRICAO_ALERTA[nivel];

      const lista = document.getElementById("vp-lista");
      if (!linhas.length) {
        lista.innerHTML = '<div class="empty-state">Nenhum CT-e neste nível para os filtros atuais.</div>';
        return;
      }
      // Clientes vêm recolhidos -- a tabela de cada um só é montada quando
      // ele é aberto (o nível crítico pode ter milhares de CT-e). Com busca
      // digitada, os clientes encontrados já abrem.
      if (vpNivelAnterior !== nivel) { vpAbertos.clear(); vpNivelAnterior = nivel; }
      const comBusca = !!vpBusca.trim();
      lista.innerHTML =
        '<div class="comp-vp-acoes"><span class="reset-link" data-acao="abrir">Expandir todos</span>' +
        '<span class="reset-link" data-acao="fechar">Recolher todos</span></div>' +
        ordem.map(([nome, rows], i) => {
          const resumo = nivel === "vencido" || nivel === "critico"
            ? "maior atraso: " + fmtN(rows[0].atraso || 0) + (rows[0].atraso === 1 ? " dia útil" : " dias úteis")
            : "próximo vencimento: " + fmtDate(rows.reduce((m, r) => (!m || r.vencimento < m ? r.vencimento : m), ""));
          const ct = contarTrat(rows);
          const trat = [ct.andamento ? fmtN(ct.andamento) + " em andamento" : "", ct.resolvido ? fmtN(ct.resolvido) + " resolvido(s)" : ""]
            .filter(Boolean).join(" · ");
          return '<details class="comp-pend comp-vp-grupo" data-i="' + i + '" style="border-left-color:' + cor + '"' +
            (comBusca || vpAbertos.has(nome) ? " open" : "") + '>' +
            '<summary class="comp-pend-head"><span class="comp-vp-seta">▸</span><strong>' + esc(nome) + '</strong>' +
            '<span class="comp-vp-qtd" style="--cor:' + cor + '">' + fmtN(rows.length) + ' CT-e</span>' +
            '<span class="comp-de">' + esc(resumo) + '</span>' +
            (trat ? '<span class="comp-vp-trat">' + esc(trat) + '</span>' : "") + '</summary>' +
            '<div class="comp-vp-corpo"></div></details>';
        }).join("");

      function preencher(det) {
        const corpo = det.querySelector(".comp-vp-corpo");
        if (corpo.dataset.ok) return;
        const rows = ordem[parseInt(det.dataset.i, 10)][1];
        corpo.innerHTML =
          '<div class="comp-pend-scroll comp-vp-scroll"><table class="detail comp-pend-table"><thead><tr>' +
          '<th>CT-e</th><th>Nota(s) fiscal(is)</th><th>Emissão</th><th>Destinatário</th><th>Cidade / UF</th><th>Entrega</th><th>Vencimento</th><th>Prazo do comprovante</th><th>Tratativa</th>' +
          '</tr></thead><tbody>' + rows.map((r, idx) =>
            '<tr>' +
            '<td>' + esc(r.cte) + (r.serie ? '<span class="bip-muted">/' + esc(r.serie) + '</span>' : "") + '</td>' +
            '<td class="comp-pend-nf">' + esc(r.notas_fiscais || "—") + '</td>' +
            '<td>' + fmtDate(r.emissao) + '</td>' +
            '<td title="' + esc(r.destinatario) + '">' + esc(truncate(r.destinatario, 30)) + '</td>' +
            '<td>' + esc(r.cidade_dest) + '/' + esc(r.uf_dest) + '</td>' +
            '<td>' + fmtDate(r.data_baixa) + '</td>' +
            '<td>' + fmtDate(r.vencimento) + '</td>' +
            '<td data-prazo="' + idx + '">' + pillPrazo(r) + '</td>' +
            '<td>' + celulaTrat(r, idx) + '</td>' +
            '</tr>'
          ).join("") + '</tbody></table></div>';
        corpo.dataset.ok = "1";
        bindTooltipsPrazo(corpo, rows);
        bindBotoesTrat(corpo, rows);
      }

      lista.querySelectorAll("details.comp-vp-grupo").forEach(det => {
        if (det.open) preencher(det);
        det.addEventListener("toggle", () => {
          if (det.open) preencher(det);
          if (comBusca) return;  // aberto pela busca -- não fica aberto depois de limpar
          const nome = ordem[parseInt(det.dataset.i, 10)][0];
          if (det.open) vpAbertos.add(nome); else vpAbertos.delete(nome);
        });
      });
      lista.querySelectorAll(".comp-vp-acoes [data-acao]").forEach(b => b.addEventListener("click", () => {
        const abrir = b.dataset.acao === "abrir";
        lista.querySelectorAll("details.comp-vp-grupo").forEach(det => { det.open = abrir; });
      }));
    }

    document.getElementById("vp-voltar").addEventListener("click", e => {
      e.preventDefault();
      history.pushState("", document.title, location.pathname + location.search);
      rotear();
    });
    document.getElementById("vp-search").addEventListener("input", function () {
      vpBusca = this.value; renderVistaPrazo();
    });
    document.getElementById("vp-export").addEventListener("click", () => {
      const nivel = nivelAberto();
      const lines = [["Cliente", "CT-e", "Serie", "Notas fiscais", "Emissao", "Destinatario", "Cidade", "UF", "Entrega",
        "Prazo (dias uteis)", "Vencimento", "Alerta", "Detalhe do prazo", "Tratativa", "Ultima tratativa", "Responsavel", "Data da tratativa"].join(";")];
      vpLinhas.forEach(r => {
        const t = ultimaTrat(r.id_cte);
        lines.push([csvSafe(r.consignatario), r.cte, r.serie || "", csvSafe(r.notas_fiscais || ""), r.emissao || "",
          csvSafe(r.destinatario), csvSafe(r.cidade_dest || ""), r.uf_dest || "", r.data_baixa || "",
          r.prazo_comprovante_dias, r.vencimento || "", ROTULO_ALERTA[r.alerta], csvSafe(textoPrazo(r)),
          t ? ROTULO_TRAT[t.situacao] : "", csvSafe(t ? t.texto : ""), csvSafe(t ? t.responsavel || "" : ""),
          t ? fmtDataHora(t.criado_em) : ""].join(";"));
      });
      baixarCsv(lines, "comprovantes_" + nivel + ".csv");
    });

    function render() {
      const recorte = getRecorte();
      const visiveis = recorte.filter(r => state.situacoes.has(r.situacao));
      renderKpis(recorte);
      renderPrazo(recorte);
      renderComposicao(visiveis);
      renderTrend(visiveis);
      renderFiliais(recorte);
      renderBusca();
      if (nivelAberto()) renderVistaPrazo();
    }

    window.addEventListener("hashchange", rotear);
    render();
    rotear();
  }

  loadData().then(data => { if (data) boot(data); });
})();
