(function () {
  "use strict";

  // Controle de entregas da operação GDC: lista com filtros (o servidor filtra
  // e pagina -- /api/gdc) e o formulário de lançamento/edição, com quantas
  // notas fiscais a guia tiver.

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
  const FILTROS = ["busca", "de", "ate", "baixado", "devolucao", "retencao"];

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

  function parametros() {
    const p = new URLSearchParams();
    FILTROS.forEach(f => { const v = $("f-" + f).value.trim(); if (v) p.set(f, v); });
    return p;
  }

  let seq = 0;
  async function carregar() {
    const meu = ++seq;
    const p = parametros();
    p.set("pagina", pagina);
    try {
      const r = await api("/api/gdc?" + p.toString());
      if (meu !== seq) return;  // chegou depois de uma busca mais nova
      itens = r.itens;
      render(r);
    } catch (e) {
      $("gdc-body").innerHTML = '<tr><td colspan="11"><div class="empty-state">' + esc(e.message) + '</div></td></tr>';
    }
  }

  function simNao(v, ruim) {
    return v ? '<span class="bip-tag ' + (ruim ? "gdc-ruim" : "faturado") + '">Sim</span>'
             : '<span class="bip-tag ' + (ruim ? "gdc-neutro" : "gdc-pendente") + '">Não</span>';
  }

  function render(r) {
    const s = r.resumo;
    $("k-total").textContent = fmtN(s.total);
    $("k-nao-baixados").textContent = fmtN(s.nao_baixados);
    $("k-devolucoes").textContent = fmtN(s.devolucoes);
    $("k-retencoes").textContent = fmtN(s.retencoes);
    $("k-palets").textContent = s.palets ? fmtN(s.palets) + " palete(s) retido(s)" : "clique para filtrar";
    $("gdc-count").textContent = fmtN(s.total) + " lançamento(s)";

    const paginas = Math.max(1, Math.ceil(s.total / r.por_pagina));
    $("gdc-paginacao").hidden = paginas <= 1;
    $("pg-info").textContent = "Página " + r.pagina + " de " + paginas;
    $("pg-anterior").disabled = r.pagina <= 1;
    $("pg-proxima").disabled = r.pagina >= paginas;

    const tbody = $("gdc-body");
    if (!itens.length) {
      const filtrado = [...parametros().keys()].length;
      tbody.innerHTML = '<tr><td colspan="11"><div class="empty-state">' +
        (filtrado ? "Nenhum lançamento com esses filtros." : 'Nenhuma entrega lançada ainda. Clique em "+ Nova entrega".') + '</div></td></tr>';
      return;
    }
    tbody.innerHTML = itens.map((e, i) =>
      '<tr>' +
      '<td class="num gdc-nowrap">' + fmtData(e.data) + '</td>' +
      '<td class="gdc-nowrap"><strong>' + esc(e.guia) + '</strong></td>' +
      '<td class="num gdc-nfs">' + (e.notas.length ? esc(e.notas.join(", ")) : '<span class="bip-muted">—</span>') + '</td>' +
      '<td>' + esc(e.destinatario) + '</td>' +
      '<td>' + esc(e.cidade) + '</td>' +
      '<td class="num">' + (e.manifesto ? esc(e.manifesto) : '<span class="bip-muted">—</span>') + '</td>' +
      '<td>' + simNao(e.manifesto_baixado, false) + '</td>' +
      '<td>' + simNao(e.devolucao, true) + '</td>' +
      '<td>' + (e.retencao_palets ? '<span class="bip-tag gdc-ruim">' + fmtN(e.qtd_palets) + '</span>' : '<span class="bip-tag gdc-neutro">Não</span>') + '</td>' +
      '<td class="gdc-obs" title="' + esc(e.observacao) + '">' + esc(e.observacao) + '</td>' +
      '<td class="bip-acoes"><button type="button" class="btn" data-editar="' + i + '">Editar</button> ' +
      '<button type="button" class="btn" data-excluir="' + i + '" title="Excluir lançamento">✕</button></td>' +
      '</tr>').join("");
    tbody.querySelectorAll("[data-editar]").forEach(b => b.addEventListener("click", () => abrir(itens[+b.dataset.editar])));
    tbody.querySelectorAll("[data-excluir]").forEach(b => b.addEventListener("click", () => excluir(itens[+b.dataset.excluir])));
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
    marcar("g-baixado", e && e.manifesto_baixado);
    marcar("g-devolucao", e && e.devolucao);
    marcar("g-retencao", e && e.retencao_palets);
    $("g-qtd-wrap").hidden = !(e && e.retencao_palets);
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
      manifesto_baixado: marcado("g-baixado"), devolucao: marcado("g-devolucao"),
      retencao_palets: marcado("g-retencao"), qtd_palets: $("g-qtd").value,
      confirmar_repetida: !!confirmarRepetida,
    };
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
  modal.addEventListener("click", ev => { if (ev.target === modal) fechar(); });
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && !modal.hidden) fechar(); });

  let espera = null;
  function filtrar() { pagina = 1; carregar(); }
  $("f-busca").addEventListener("input", () => { clearTimeout(espera); espera = setTimeout(filtrar, 300); });
  ["de", "ate", "baixado", "devolucao", "retencao"].forEach(f => $("f-" + f).addEventListener("change", filtrar));
  $("f-limpar").addEventListener("click", () => { FILTROS.forEach(f => { $("f-" + f).value = ""; }); filtrar(); });
  document.querySelectorAll("[data-filtro]").forEach(b => b.addEventListener("click", () => {
    const sel = $("f-" + b.dataset.filtro);
    sel.value = sel.value === b.dataset.valor ? "" : b.dataset.valor;
    filtrar();
  }));
  $("pg-anterior").addEventListener("click", () => { pagina--; carregar(); });
  $("pg-proxima").addEventListener("click", () => { pagina++; carregar(); });
  $("btn-exportar").addEventListener("click", () => { window.location.href = "/api/gdc/exportar?" + parametros().toString(); });

  carregar();
})();
