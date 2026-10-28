(function () {
  "use strict";

  // Aba "Vários de uma vez": cola/bipa várias chaves, mesmo tipo. Cada chave
  // vira um pedido; o robô do PC vai emitindo em sequência. Com "salvar
  // direto", os que batem já saem salvos e só as exceções ficam aqui.

  const C = window.emissaoChat;
  const CHAVE_LOTE = "emissao_lote_atual";
  const EM_ANDAMENTO = ["preparando", "salvando"];
  const PRECISA = ["escolher", "conferir"];

  const fTipo = document.getElementById("lt-tipo");
  const fChaves = document.getElementById("lt-chaves");
  const fSolicitante = document.getElementById("lt-solicitante");
  const fDireto = document.getElementById("lt-direto");
  const btnEnviar = document.getElementById("lt-enviar");
  const contagem = document.getElementById("lt-contagem");
  const statusEl = document.getElementById("lt-status");
  const corpo = document.getElementById("lt-body");

  let loteAtual = C.ler(CHAVE_LOTE);
  let pedidos = [];
  let timer = null;

  // Devolução aparece como duas opções (com/sem NFD): no lote não dá pra
  // perguntar uma a uma.
  const ROTULO = { devolucao: "Devolução sem NFD (chave da nota original)", devolucao_nfd: "Devolução com NFD (chave da NFD)" };
  fTipo.innerHTML = '<option value="">Selecione…</option>' + C.TIPOS.map(t =>
    '<option value="' + t.valor + '">' + t.icone + " " + C.esc(ROTULO[t.valor] || t.rotulo) + "</option>").join("");

  if (C.SOLICITANTE_FIXO) document.getElementById("lt-solicitante-campo").hidden = true;
  else fSolicitante.value = C.ler("emissao_solicitante") || "";

  function chavesDigitadas() {
    return fChaves.value.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  }

  function problema(linha) {
    const dig = linha.replace(/\D/g, "");
    if (dig.length !== 44) return "tem " + dig.length + " dígitos";
    if (!C.dvConfere(dig)) return "dígito verificador não confere";
    if (C.dadosChave(dig).modelo !== "55") return "não é chave de NF-e";
    return null;
  }

  function atualizarContagem() {
    const linhas = chavesDigitadas();
    if (!linhas.length) {
      contagem.textContent = "Nenhuma chave ainda.";
      contagem.className = "cv-dica";
      btnEnviar.disabled = true;
      btnEnviar.textContent = "Enviar";
      return;
    }
    const ruins = linhas.map((l, i) => [i + 1, problema(l)]).filter(x => x[1]);
    const unicas = new Set(linhas.map(l => l.replace(/\D/g, ""))).size;
    const repetidas = linhas.length - unicas;
    const boas = linhas.length - ruins.length - repetidas;
    contagem.className = "cv-dica" + (ruins.length ? " erro" : " ok");
    contagem.textContent = linhas.length + " chave(s) · " + Math.max(boas, 0) + " ok" +
      (repetidas ? " · " + repetidas + " repetida(s)" : "") +
      (ruins.length ? " · com problema: " + ruins.slice(0, 4).map(x => "linha " + x[0] + " (" + x[1] + ")").join(", ") +
        (ruins.length > 4 ? "…" : "") + " — essas ficam de fora" : "");
    btnEnviar.disabled = !fTipo.value || boas <= 0;
    btnEnviar.textContent = boas > 0 ? "Enviar " + boas + " CT-e" : "Enviar";
  }
  fChaves.addEventListener("input", atualizarContagem);
  fTipo.addEventListener("change", atualizarContagem);

  document.getElementById("lt-form").addEventListener("submit", async e => {
    e.preventDefault();
    const solicitante = C.SOLICITANTE_FIXO || fSolicitante.value.trim();
    if (!solicitante) { fSolicitante.focus(); statusEl.textContent = "Informe quem está pedindo."; return; }
    if (!C.SOLICITANTE_FIXO) C.guardar("emissao_solicitante", solicitante);
    btnEnviar.disabled = true;
    statusEl.textContent = "Enviando…";
    const r = await C.api("/api/emissoes/lote", {
      method: "POST",
      body: JSON.stringify({ tipo: fTipo.value, chaves: chavesDigitadas(), solicitante, salvar_direto: fDireto.checked }),
    }).catch(err => ({ ok: false, body: { erro: err.message } }));
    btnEnviar.disabled = false;
    if (!r.ok) { statusEl.textContent = "Erro: " + (r.body.erro || "falha ao enviar"); return; }
    statusEl.textContent = r.body.criados + " pedido(s) enviados" +
      (r.body.robo_ligado ? " — o robô do PC já está emitindo." : " — o robô do PC está desligado: vão pelo GitHub, um por vez (bem mais devagar).");
    fChaves.value = "";
    atualizarContagem();
    loteAtual = r.body.lote;
    C.guardar(CHAVE_LOTE, loteAtual);
    mostrarRecusadas(r.body.recusadas || []);
    await carregar();
    C.carregarRecentes();
  });

  function mostrarRecusadas(lista) {
    const el = document.getElementById("lt-recusadas");
    el.innerHTML = lista.length
      ? "<b>Ficaram de fora (" + lista.length + "):</b> " + lista.map(x =>
        (x.chave.length === 44 ? "NF " + C.esc(C.nf(x.chave)) : C.esc(x.chave.slice(0, 20))) + " — " + C.esc(x.motivo)).join("; ")
      : "";
  }

  function situacao(em) {
    if (em.status === "salvo") return "✅ Salvo · CT-e " + C.esc(em.numero_ct);
    if (em.status === "conferir" && em.frete_calculado == null) return "✋ Falta o frete";
    if (em.status === "conferir") return "✋ Conferir";
    if (em.status === "escolher") return "✋ Escolher o CT-e";
    if (em.status === "erro") return '<span class="flag-no" title="' + C.esc(em.erro_mensagem || "") + '">Erro: ' +
      C.esc((em.erro_mensagem || "").slice(0, 80)) + "</span>";
    if (em.status === "cancelado") return "Cancelado";
    return em.executor && em.executor.indexOf("local:") === 0 ? "⏳ Emitindo agora…" : "⏳ Na fila";
  }

  function render() {
    const n = s => pedidos.filter(em => s.includes(em.status)).length;
    const total = pedidos.length;
    const salvos = n(["salvo"]), precisa = n(PRECISA), erros = n(["erro"]), fila = n(EM_ANDAMENTO);
    const feitos = total - fila;
    document.getElementById("lt-andamento").hidden = !total;
    document.getElementById("lt-resumo").innerHTML =
      "<b>Lote de " + total + " CT-e</b> · ✅ " + salvos + " salvo(s) · ⏳ " + fila + " na fila · ✋ " + precisa +
      " esperando você · " + erros + " erro(s)";
    document.getElementById("lt-barra").style.width = (total ? Math.round(feitos / total * 100) : 0) + "%";

    const prontos = pedidos.filter(em => em.status === "conferir" && em.frete_calculado != null);
    document.getElementById("lt-acoes").innerHTML = prontos.length
      ? '<button type="button" class="btn-primary" id="lt-salvar-todos">💾 Salvar os ' + prontos.length +
        " que estão para conferir, com o frete calculado pelo GW</button>"
      : "";

    corpo.innerHTML = pedidos.map(em => {
      const r = em.resumo || {};
      const frete = em.frete_final != null ? em.frete_final : em.frete_calculado;
      const acao = PRECISA.includes(em.status) || em.status === "erro"
        ? '<button type="button" class="btn" data-resolver="' + em.id + '">' + (em.status === "erro" ? "Ver" : "Resolver") + "</button>"
        : "";
      return "<tr>" +
        '<td title="' + C.esc(em.chave_nfe) + '">' + C.esc(C.nf(em.chave_nfe)) + "</td>" +
        "<td>" + C.esc(r.cte_original || (r.remetente ? "— (da nota)" : "")) + "</td>" +
        "<td>" + C.esc(r.destinatario || "") + "</td>" +
        "<td>" + (frete != null ? C.moeda(frete) : "") + "</td>" +
        "<td>" + situacao(em) + "</td>" +
        "<td>" + acao + "</td></tr>";
    }).join("");

    const btnTodos = document.getElementById("lt-salvar-todos");
    if (btnTodos) btnTodos.addEventListener("click", () => salvarTodos(prontos));
  }

  async function salvarTodos(lista) {
    const total = lista.reduce((t, em) => t + em.frete_calculado, 0);
    if (!confirm("Salvar " + lista.length + " CT-e no GW (sem transmitir), com o frete calculado pelo GW? Total: " + C.moeda(total) + ".")) return;
    for (const em of lista) {
      await C.api("/api/emissoes/" + em.id + "/salvar", { method: "POST", body: JSON.stringify({ frete: em.frete_calculado }) })
        .catch(() => null);
    }
    await carregar();
  }

  corpo.addEventListener("click", e => {
    const btn = e.target.closest("button[data-resolver]");
    if (!btn) return;
    const em = pedidos.find(x => String(x.id) === btn.dataset.resolver);
    if (em) C.abrirNaConversa(em);
  });

  async function carregar() {
    if (!loteAtual) return;
    const r = await C.api("/api/emissoes?lote=" + encodeURIComponent(loteAtual)).catch(() => null);
    if (!r || !r.ok) return;
    pedidos = r.body.emissoes || [];
    render();
    const andando = pedidos.some(em => EM_ANDAMENTO.includes(em.status));
    if (andando && !timer) timer = setInterval(carregar, 5000);
    if (!andando && timer) { clearInterval(timer); timer = null; C.carregarRecentes(); }
  }

  atualizarContagem();
  carregar();
})();
