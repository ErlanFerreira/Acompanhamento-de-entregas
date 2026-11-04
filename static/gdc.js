(function () {
  "use strict";

  // Controle de entregas da operação GDC: as guias em cards, com filtros (o
  // servidor filtra e pagina -- /api/gdc), em duas abas -- Em aberto e Arquivo
  // (as concluídas) -- e o formulário de lançamento/edição, com quantas notas
  // fiscais a guia tiver (e NFD + observações quando houve devolução).

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function fmtN(n) { return Number(n || 0).toLocaleString("pt-BR"); }
  function fmtData(iso) { return iso ? iso.slice(0, 10).split("-").reverse().join("/") : ""; }
  function fmtDataHora(iso) { return iso ? fmtData(iso) + " " + iso.slice(11, 16) : ""; }
  function hojeIso() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  const $ = id => document.getElementById(id);
  const modal = $("gdc-modal");
  const FILTROS = ["busca", "de", "ate", "baixado", "gw", "portal", "devolucao", "retencao"];
  // As baixas (sim/não + data quando sim): id no formulário, campo e campo da data na API.
  const BAIXAS = [
    { id: "baixado", campo: "manifesto_baixado", data: "data_baixa", nome: "Baixa do manifesto" },
    { id: "gw", campo: "baixa_gw", data: "data_baixa_gw", nome: "Baixa no GW" },
    { id: "portal", campo: "baixa_portal", data: "data_baixa_portal", nome: "Baixa no portal GDC" },
  ];

  let itens = [];
  let pagina = 1;
  let editando = null;  // lançamento em edição (null = novo)

  async function api(url, opts) {
    const resp = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, opts || {}));
    if (resp.status === 401) { window.location.href = "/login"; throw new Error("Sessão expirada."); }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) { const e = new Error(body.erro || body.detail || "Falha na operação."); e.body = body; e.status = resp.status; throw e; }
    return body;
  }

  // ------------------------------------------------------------ lista ----

  let aba = "abertos";  // "abertos" (lista de trabalho) ou "arquivo" (concluídas)

  function parametros() {
    const p = new URLSearchParams();
    FILTROS.forEach(f => { const v = $("f-" + f).value.trim(); if (v) p.set(f, v); });
    return p;
  }

  let seq = 0;
  async function carregar() {
    const meu = ++seq;
    const p = parametros();
    p.set("situacao", aba);
    p.set("pagina", pagina);
    try {
      const r = await api("/api/gdc?" + p.toString());
      if (meu !== seq) return;  // chegou depois de uma busca mais nova
      itens = r.itens;
      render(r);
    } catch (e) {
      $("gdc-cards").innerHTML = '<div class="empty-state">' + esc(e.message) + '</div>';
    }
  }

  function simNao(v, ruim) {
    return v ? '<span class="bip-tag ' + (ruim ? "gdc-ruim" : "faturado") + '">Sim</span>'
             : '<span class="bip-tag ' + (ruim ? "gdc-neutro" : "gdc-pendente") + '">Não</span>';
  }

  function selo(texto, tipo) { return '<span class="gdc-selo ' + tipo + '">' + texto + '</span>'; }

  function pendentes(e) { return BAIXAS.filter(b => !e[b.campo]).map(b => b.nome); }

  // Um card por guia: selos da situação no topo, destinatário, dados da
  // entrega e, embaixo, as baixas/ocorrências com os botões.
  function card(e, i) {
    const falta = pendentes(e);
    const selos =
      (e.concluido ? selo("✓ Concluída", "ok") : falta.length ? selo("Baixa pendente", "aviso") : selo("Tudo baixado", "ok")) +
      (e.devolucao ? selo("Devolução", "critico") : "") +
      (e.retencao_palets ? selo(fmtN(e.qtd_palets) + " palete(s) retido(s)", "critico") : "");
    const meta = [
      e.cidade ? '<span title="Cidade">📍 ' + esc(e.cidade) + '</span>' : "",
      '<span title="Notas fiscais">🧾 NF ' + (e.notas.length ? esc(e.notas.join(", ")) : "—") + '</span>',
      '<span title="Manifesto">📄 Manifesto ' + (e.manifesto ? esc(e.manifesto) : "—") + '</span>',
      '<span title="Lançado por">👤 ' + esc(e.criado_por) + '</span>',
      '<span title="Lançado em">🕓 ' + fmtDataHora(e.criado_em) + '</span>',
    ].join("");
    const baixas = BAIXAS.map(b => '<span class="gdc-item"><span class="gdc-item-k">' + b.nome + '</span> ' +
      simNao(e[b.campo], false) + (e[b.data] ? ' <span class="gdc-nfd">' + fmtData(e[b.data]) + '</span>' : "") + '</span>').join("");
    const dev = '<span class="gdc-item"><span class="gdc-item-k">Devolução</span> ' + simNao(e.devolucao, true) +
      (e.devolucao ? (e.nfd ? ' <span class="gdc-nfd">NFD ' + esc(e.nfd) + '</span>' : ' <span class="bip-tag gdc-pendente">sem NFD</span>') : "") + '</span>';
    const palets = '<span class="gdc-item"><span class="gdc-item-k">Paletes retidos</span> ' +
      (e.retencao_palets ? '<span class="bip-tag gdc-ruim">' + fmtN(e.qtd_palets) + '</span>' : '<span class="bip-tag gdc-neutro">Não</span>') + '</span>';
    const acoes = e.concluido
      ? '<button type="button" class="btn" data-reabrir="' + i + '" title="Volta para a lista Em aberto">↺ Reabrir</button>' +
        '<button type="button" class="btn" data-editar="' + i + '">Editar</button>'
      : '<button type="button" class="btn gdc-btn-concluir" data-concluir="' + i + '">✓ Concluir</button>' +
        '<button type="button" class="btn" data-editar="' + i + '">Editar</button>' +
        '<button type="button" class="btn gdc-btn-excluir" data-excluir="' + i + '">Excluir</button>';
    const notas = [
      e.devolucao && e.obs_devolucao ? '<div class="gdc-card-obs"><strong>Devolução:</strong> ' + esc(e.obs_devolucao) + '</div>' : "",
      e.observacao ? '<div class="gdc-card-obs">💬 ' + esc(e.observacao) + '</div>' : "",
      e.concluido ? '<div class="gdc-card-obs gdc-concluida">✓ Concluída em ' + fmtDataHora(e.concluido_em) +
        (e.concluido_por ? " por " + esc(e.concluido_por) : "") + '</div>' : "",
    ].join("");
    return '<article class="gdc-card">' +
      '<div class="gdc-card-topo"><h3>Guia ' + esc(e.guia) + '</h3><span class="gdc-card-data">' + fmtData(e.data) + '</span>' + selos + '</div>' +
      '<div class="gdc-card-dest" title="' + esc(e.destinatario) + '">' + esc(e.destinatario) + '</div>' +
      '<div class="gdc-card-meta">' + meta + '</div>' +
      '<div class="gdc-card-status"><div class="gdc-itens">' + baixas + dev + palets + '</div>' +
        '<div class="gdc-card-acoes">' + acoes + '</div></div>' +
      notas +
      '</article>';
  }

  function render(r) {
    const s = r.resumo;
    $("k-total").textContent = fmtN(s.total);
    $("k-total-sub").textContent = aba === "arquivo" ? "no arquivo" : "em aberto";
    $("k-nao-baixados").textContent = fmtN(s.nao_baixados);
    $("k-devolucoes").textContent = fmtN(s.devolucoes);
    $("k-retencoes").textContent = fmtN(s.retencoes);
    $("k-palets").textContent = s.palets ? fmtN(s.palets) + " palete(s) retido(s)" : "clique para filtrar";
    $("aba-abertos").textContent = fmtN(r.abas.abertos);
    $("aba-arquivo").textContent = fmtN(r.abas.arquivo);
    $("gdc-count").textContent = fmtN(s.total) + " guia(s)";

    const paginas = Math.max(1, Math.ceil(s.total / r.por_pagina));
    $("gdc-paginacao").hidden = paginas <= 1;
    $("pg-info").textContent = "Página " + r.pagina + " de " + paginas;
    $("pg-anterior").disabled = r.pagina <= 1;
    $("pg-proxima").disabled = r.pagina >= paginas;

    const lista = $("gdc-cards");
    if (!itens.length) {
      const filtrado = [...parametros().keys()].length;
      lista.innerHTML = '<div class="empty-state">' + (filtrado ? "Nenhuma guia com esses filtros nesta aba." :
        aba === "arquivo" ? "Nenhuma guia concluída ainda." : 'Nenhuma guia em aberto. Clique em "+ Nova entrega".') + '</div>';
      return;
    }
    lista.innerHTML = itens.map(card).join("");
    lista.querySelectorAll("[data-editar]").forEach(b => b.addEventListener("click", () => abrir(itens[+b.dataset.editar])));
    lista.querySelectorAll("[data-excluir]").forEach(b => b.addEventListener("click", () => excluir(itens[+b.dataset.excluir])));
    lista.querySelectorAll("[data-concluir]").forEach(b => b.addEventListener("click", () => concluir(itens[+b.dataset.concluir], true)));
    lista.querySelectorAll("[data-reabrir]").forEach(b => b.addEventListener("click", () => concluir(itens[+b.dataset.reabrir], false)));
  }

  function trocarAba(nova) {
    aba = nova;
    pagina = 1;
    document.querySelectorAll(".gdc-aba").forEach(b => b.classList.toggle("ativa", b.dataset.aba === aba));
    carregar();
  }

  // Concluir manda a guia pro Arquivo; reabrir traz de volta pra lista.
  async function concluir(e, sim) {
    const falta = pendentes(e);
    const msg = sim
      ? "Concluir a guia " + e.guia + "? Ela sai da lista e vai para o Arquivo." +
        (falta.length ? "\n\nAtenção, ainda pendente: " + falta.join(", ") + "." : "")
      : "Reabrir a guia " + e.guia + "? Ela volta para a lista Em aberto.";
    if (!confirm(msg)) return;
    try {
      await api("/api/gdc/" + e.id + (sim ? "/concluir" : "/reabrir"), { method: "POST" });
      await carregar();
    } catch (err) { alert(err.message); }
  }

  async function excluir(e) {
    if (!confirm("Excluir o lançamento da guia " + e.guia + " (" + fmtData(e.data) + ")? Não dá para desfazer.")) return;
    try {
      await api("/api/gdc/" + e.id, { method: "DELETE" });
      await carregar();
    } catch (err) { alert(err.message); }
  }

  // ------------------------------------------------------- formulário ----

  function addNota(valor) {
    const linha = document.createElement("div");
    linha.className = "gdc-nota";
    linha.innerHTML = '<input type="text" inputmode="numeric" maxlength="20" placeholder="Nº da NF" autocomplete="off">' +
      '<button type="button" class="btn" aria-label="Remover NF">✕</button>';
    const input = linha.querySelector("input");
    input.value = valor || "";
    // Enter numa NF já abre a próxima (bom pra digitar várias em sequência).
    input.addEventListener("keydown", ev => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      const prox = linha.nextElementSibling;
      if (prox) prox.querySelector("input").focus(); else addNota("").querySelector("input").focus();
    });
    linha.querySelector("button").addEventListener("click", () => {
      linha.remove();
      if (!$("g-notas").children.length) addNota("");
    });
    $("g-notas").appendChild(linha);
    return linha;
  }

  function marcar(nome, valor) {
    document.querySelector('input[name="' + nome + '"][value="' + (valor ? "sim" : "nao") + '"]').checked = true;
  }
  function marcado(nome) { return document.querySelector('input[name="' + nome + '"]:checked').value === "sim"; }

  function atualizarQtd() {
    const ret = marcado("g-retencao");
    $("g-qtd-wrap").hidden = !ret;
    if (ret && !$("g-qtd").value) $("g-qtd").focus();
  }

  // Ao marcar uma baixa como "Sim", a data já vem com hoje (dá pra trocar).
  function atualizarBaixa(b) {
    const sim = marcado("g-" + b.id);
    $("g-" + b.id + "-wrap").hidden = !sim;
    if (sim && !$("g-" + b.id + "-data").value) $("g-" + b.id + "-data").value = hojeIso();
  }

  function atualizarDevolucao() {
    const dev = marcado("g-devolucao");
    $("g-dev-wrap").hidden = !dev;
    if (dev && !$("g-nfd").value) $("g-nfd").focus();
  }

  function abrir(e) {
    editando = e || null;
    $("gdc-titulo").textContent = e ? "Editar entrega — guia " + e.guia : "Nova entrega";
    $("g-guia").value = e ? e.guia : "";
    $("g-data").value = e ? e.data : hojeIso();
    $("g-destinatario").value = e ? e.destinatario : "";
    $("g-cidade").value = e ? e.cidade : "";
    $("g-manifesto").value = e ? e.manifesto : "";
    $("g-obs").value = e ? e.observacao : "";
    $("g-qtd").value = e && e.qtd_palets ? e.qtd_palets : "";
    BAIXAS.forEach(b => {
      marcar("g-" + b.id, e && e[b.campo]);
      $("g-" + b.id + "-data").value = e && e[b.data] ? e[b.data] : "";
      $("g-" + b.id + "-wrap").hidden = !(e && e[b.campo]);
    });
    $("g-nfd").value = e ? e.nfd : "";
    $("g-obs-dev").value = e ? e.obs_devolucao : "";
    marcar("g-devolucao", e && e.devolucao);
    marcar("g-retencao", e && e.retencao_palets);
    $("g-qtd-wrap").hidden = !(e && e.retencao_palets);
    $("g-dev-wrap").hidden = !(e && e.devolucao);
    $("g-notas").innerHTML = "";
    (e && e.notas.length ? e.notas : [""]).forEach(addNota);
    $("g-autoria").textContent = e
      ? "Lançado por " + e.criado_por + " em " + fmtDataHora(e.criado_em) +
        (e.atualizado_em ? " · alterado por " + e.atualizado_por + " em " + fmtDataHora(e.atualizado_em) : "")
      : "";
    erro("");
    modal.hidden = false;
    $("g-guia").focus();
  }

  function fechar() { modal.hidden = true; editando = null; }

  function erro(msg) {
    $("gdc-erro").textContent = msg;
    $("gdc-erro").classList.toggle("show", !!msg);
  }

  async function salvar(ev, confirmarRepetida) {
    if (ev) ev.preventDefault();
    const payload = {
      guia: $("g-guia").value, data: $("g-data").value, destinatario: $("g-destinatario").value,
      cidade: $("g-cidade").value, manifesto: $("g-manifesto").value, observacao: $("g-obs").value,
      notas: [...$("g-notas").querySelectorAll("input")].map(i => i.value.trim()).filter(Boolean),
      devolucao: marcado("g-devolucao"), nfd: $("g-nfd").value, obs_devolucao: $("g-obs-dev").value,
      retencao_palets: marcado("g-retencao"), qtd_palets: $("g-qtd").value,
      confirmar_repetida: !!confirmarRepetida,
    };
    BAIXAS.forEach(b => { payload[b.campo] = marcado("g-" + b.id); payload[b.data] = $("g-" + b.id + "-data").value; });
    const botao = $("gdc-salvar");
    botao.disabled = true;
    try {
      await api(editando ? "/api/gdc/" + editando.id : "/api/gdc", { method: editando ? "PUT" : "POST", body: JSON.stringify(payload) });
      fechar();
      await carregar();
    } catch (e) {
      if (e.status === 409 && e.body && e.body.repetida) {
        botao.disabled = false;
        if (confirm(e.message + "\n\nLançar mesmo assim?")) return salvar(null, true);
        return;
      }
      erro(e.message);
    } finally {
      botao.disabled = false;
    }
  }

  // ---------------------------------------------------------- eventos ----

  $("btn-novo").addEventListener("click", () => abrir(null));
  $("gdc-fechar").addEventListener("click", fechar);
  $("gdc-cancelar").addEventListener("click", fechar);
  $("gdc-form").addEventListener("submit", salvar);
  $("g-add-nota").addEventListener("click", () => addNota("").querySelector("input").focus());
  document.querySelectorAll('input[name="g-retencao"]').forEach(r => r.addEventListener("change", atualizarQtd));
  BAIXAS.forEach(b => document.querySelectorAll('input[name="g-' + b.id + '"]').forEach(r => r.addEventListener("change", () => atualizarBaixa(b))));
  document.querySelectorAll('input[name="g-devolucao"]').forEach(r => r.addEventListener("change", atualizarDevolucao));
  modal.addEventListener("click", ev => { if (ev.target === modal) fechar(); });
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && !modal.hidden) fechar(); });

  let espera = null;
  function filtrar() { pagina = 1; carregar(); }
  $("f-busca").addEventListener("input", () => { clearTimeout(espera); espera = setTimeout(filtrar, 300); });
  FILTROS.slice(1).forEach(f => $("f-" + f).addEventListener("change", filtrar));
  $("f-limpar").addEventListener("click", () => { FILTROS.forEach(f => { $("f-" + f).value = ""; }); filtrar(); });
  document.querySelectorAll("[data-filtro]").forEach(b => b.addEventListener("click", () => {
    const sel = $("f-" + b.dataset.filtro);
    sel.value = sel.value === b.dataset.valor ? "" : b.dataset.valor;
    filtrar();
  }));
  $("pg-anterior").addEventListener("click", () => { pagina--; carregar(); });
  $("pg-proxima").addEventListener("click", () => { pagina++; carregar(); });
  $("btn-exportar").addEventListener("click", () => {
    const p = parametros();
    p.set("situacao", aba);
    window.location.href = "/api/gdc/exportar?" + p.toString();
  });
  document.querySelectorAll(".gdc-aba").forEach(b => b.addEventListener("click", () => trocarAba(b.dataset.aba)));

  carregar();
})();
