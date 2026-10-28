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
  // m³ digitados nas linhas "Falta a cubagem" -- a tabela é redesenhada a
  // cada 5 s e não pode apagar o que a pessoa digitou.
  const cubDigitada = {};

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

  // Cada linha: "chave" ou "chave;m³" (cubagem, pro frete por peso/cubagem).
  function partes(linha) {
    const [chave, cub] = linha.split(/[;\t ]+/);
    return { chave: (chave || "").replace(/\D/g, ""), cub: (cub || "").trim() };
  }

  function problema(linha) {
    const { chave: dig, cub } = partes(linha);
    if (cub && !(parseFloat(cub.replace(",", ".")) > 0)) return "cubagem inválida";
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
    const unicas = new Set(linhas.map(l => partes(l).chave)).size;
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

  // XML de NF-e: lidos aqui no navegador (não sobem pro servidor) só pra
  // tirar a chave -- quem importa a nota de verdade é o GW, pela chave.
  async function lerXmls(arquivos) {
    const xmls = Array.from(arquivos).filter(f => /\.xml$/i.test(f.name));
    const status = document.getElementById("lt-xml-status");
    if (!xmls.length) { status.textContent = "Nenhum arquivo .xml escolhido."; return; }
    const notas = [], fora = [];
    for (const f of xmls) {
      const doc = new DOMParser().parseFromString(await f.text(), "application/xml");
      const inf = doc.getElementsByTagNameNS("*", "infNFe")[0];
      const campo = (pai, nome) => { const el = pai && pai.getElementsByTagNameNS("*", nome)[0]; return el ? el.textContent.trim() : ""; };
      const chave = inf ? (inf.getAttribute("Id") || "").replace(/\D/g, "") : "";
      if (!inf || chave.length !== 44) { fora.push(f.name); continue; }
      const emit = inf.getElementsByTagNameNS("*", "emit")[0], dest = inf.getElementsByTagNameNS("*", "dest")[0];
      notas.push({ chave, nf: campo(inf, "nNF"), emit: campo(emit, "xNome"), dest: campo(dest, "xNome") });
    }
    const atuais = new Set(chavesDigitadas().map(l => partes(l).chave));
    const novas = notas.filter(n => !atuais.has(n.chave));
    if (novas.length) {
      fChaves.value = (fChaves.value.trim() ? fChaves.value.trim() + "\n" : "") + novas.map(n => n.chave).join("\n");
    }
    if (!fTipo.value && novas.length) fTipo.value = "normal"; // XML de NF-e sem CT-e: o caso comum
    atualizarContagem();
    status.innerHTML = "<b>" + notas.length + " nota(s) lida(s)</b>" +
      (notas.length - novas.length ? " (" + (notas.length - novas.length) + " já estavam na lista)" : "") +
      (fora.length ? " · <span class=\"flag-no\">" + fora.length + " arquivo(s) não são NF-e e ficaram de fora: " +
        C.esc(fora.slice(0, 3).join(", ")) + (fora.length > 3 ? "…" : "") + "</span>" : "") +
      (notas.length ? "<br>" + notas.slice(0, 6).map(n => "NF " + C.esc(n.nf) + " — " + C.esc(n.emit) + " → " + C.esc(n.dest)).join("<br>") +
        (notas.length > 6 ? "<br>… e mais " + (notas.length - 6) : "") : "");
  }
  for (const id of ["lt-xml-arquivos", "lt-xml-pasta"]) {
    const input = document.getElementById(id);
    input.addEventListener("change", async () => { await lerXmls(input.files); input.value = ""; });
  }
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
    if (em.status === "conferir" && em.frete_calculado == null && (em.resumo || {}).precisa_cubagem) return "✋ Falta a cubagem";
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
    const semCubagem = pedidos.filter(faltaCubagem);
    document.getElementById("lt-acoes").innerHTML =
      (prontos.length
        ? '<button type="button" class="btn-primary" id="lt-salvar-todos">💾 Salvar os ' + prontos.length +
          " que estão para conferir, com o frete calculado pelo GW</button> "
        : "") +
      (semCubagem.length > 1
        ? '<button type="button" class="btn" id="lt-calcular-todos">📐 Calcular todos com cubagem preenchida</button>'
        : "");

    corpo.innerHTML = pedidos.map(em => {
      const r = em.resumo || {};
      const frete = em.frete_final != null ? em.frete_final : em.frete_calculado;
      let acao = PRECISA.includes(em.status) || em.status === "erro"
        ? '<button type="button" class="btn" data-resolver="' + em.id + '">' + (em.status === "erro" ? "Ver" : "Resolver") + "</button>"
        : "";
      if (faltaCubagem(em)) {
        acao = '<span class="lt-cub"><input type="text" inputmode="decimal" placeholder="m³" data-cub="' + em.id +
          '" value="' + C.esc(cubDigitada[em.id] || "") + '" aria-label="Cubagem em m³ da NF ' + C.esc(C.nf(em.chave_nfe)) + '">' +
          '<button type="button" class="btn" data-calcular="' + em.id + '">Calcular</button></span>';
      }
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
    const btnCalc = document.getElementById("lt-calcular-todos");
    if (btnCalc) btnCalc.addEventListener("click", () => calcular(semCubagem.filter(em => cubDigitada[em.id])));
  }

  function faltaCubagem(em) {
    return em.status === "conferir" && em.frete_calculado == null && (em.resumo || {}).precisa_cubagem;
  }

  // Manda a cubagem: o robô prepara de novo e o GW calcula o frete pela
  // tabela (com "salvar direto", já sai salvo).
  async function calcular(lista) {
    const erros = [];
    for (const em of lista) {
      const t = String(cubDigitada[em.id] || "").trim();
      const v = parseFloat(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t); // "1,25" ou "1.25"
      if (!(v > 0)) { erros.push("NF " + C.nf(em.chave_nfe)); continue; }
      const r = await C.api("/api/emissoes/" + em.id + "/cubagem", { method: "POST", body: JSON.stringify({ cubagem: v }) })
        .catch(err => ({ ok: false, body: { erro: err.message } }));
      if (r.ok) delete cubDigitada[em.id];
      else erros.push("NF " + C.nf(em.chave_nfe) + " (" + (r.body.erro || "erro") + ")");
    }
    if (erros.length) alert("Cubagem não enviada: " + erros.join(", ") + ". Use números como 1,25.");
    await carregar();
  }

  corpo.addEventListener("input", e => {
    if (e.target.dataset.cub) cubDigitada[e.target.dataset.cub] = e.target.value;
  });
  corpo.addEventListener("keydown", e => {
    if (e.key === "Enter" && e.target.dataset.cub) {
      const em = pedidos.find(x => String(x.id) === e.target.dataset.cub);
      if (em) calcular([em]);
    }
  });

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
    const calc = e.target.closest("button[data-calcular]");
    if (calc) {
      const em = pedidos.find(x => String(x.id) === calc.dataset.calcular);
      if (em) calcular([em]);
      return;
    }
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
    // Não redesenha enquanto alguém digita a cubagem (perderia o cursor).
    if (!(document.activeElement && document.activeElement.dataset && document.activeElement.dataset.cub)) render();
    const andando = pedidos.some(em => EM_ANDAMENTO.includes(em.status));
    if (andando && !timer) timer = setInterval(carregar, 5000);
    if (!andando && timer) { clearInterval(timer); timer = null; C.carregarRecentes(); }
  }

  atualizarContagem();
  carregar();
})();
