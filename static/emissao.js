(function () {
  "use strict";

  // Assistente de emissão em formato de conversa: uma pergunta por vez,
  // respostas por botão ou campo. Quem faz o trabalho no GW é o bot do
  // GitHub Actions (scripts/run_emissao_cte.py) -- a tela cria o pedido,
  // acompanha o andamento e repassa as decisões (qual CT-e, frete, salvar).

  const TIPOS = JSON.parse(document.getElementById("cv-tipos").textContent);
  const TIPO = Object.fromEntries(TIPOS.map(t => [t.valor, t]));
  // Analista entra com login próprio: o pedido já sai no nome dele.
  const SOLICITANTE_FIXO = JSON.parse(document.getElementById("cv-solicitante").textContent);
  const log = document.getElementById("cv-log");
  const recentesBody = document.getElementById("cv-recentes-body");

  const STATUS_LABEL = {
    preparando: "⏳ Buscando no GW",
    escolher: "Aguardando escolha",
    conferir: "Aguardando conferência",
    salvando: "⏳ Salvando no GW",
    salvo: "✅ Salvo (não transmitido)",
    erro: "Erro",
    cancelado: "Cancelado",
  };
  const EM_ANDAMENTO = ["preparando", "escolher", "conferir", "salvando"];
  const CHAVE_ATUAL = "emissao_atual";
  const CHAVE_NOME = "emissao_solicitante";

  let estado = {};
  let espera = null; // {timer, relogio} do "procurando no GW..."

  // ------------------------------------------------------------ util ----

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

  function moeda(v) {
    return Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function lerValor(texto) {
    let t = String(texto || "").replace(/[R$\s]/g, "");
    t = t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t;
    const v = parseFloat(t);
    return isFinite(v) ? Math.round(v * 100) / 100 : NaN;
  }

  // Mensagem do servidor dentro de uma frase nossa: sem ponto final dobrado.
  function msgErro(texto) { return esc(String(texto || "erro desconhecido").replace(/[.\s]+$/, "")) + "."; }

  function fmtData(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function mmss(seg) { return Math.floor(seg / 60) + ":" + String(seg % 60).padStart(2, "0"); }

  function guardar(chave, valor) {
    try { valor == null ? localStorage.removeItem(chave) : localStorage.setItem(chave, valor); } catch (e) { /* sem storage */ }
  }
  function ler(chave) { try { return localStorage.getItem(chave); } catch (e) { return null; } }

  // Mesmo cálculo do app/chave_cte.py (módulo 11, pesos 2..9).
  function dvConfere(chave) {
    let soma = 0, peso = 2;
    for (let i = 42; i >= 0; i--) {
      soma += Number(chave[i]) * peso;
      peso = peso === 9 ? 2 : peso + 1;
    }
    const resto = soma % 11;
    return (resto < 2 ? 0 : 11 - resto) === Number(chave[43]);
  }

  function dadosChave(chave) {
    return {
      numero: String(parseInt(chave.slice(25, 34), 10)),
      serie: String(parseInt(chave.slice(22, 25), 10)),
      emissao: chave.slice(4, 6) + "/20" + chave.slice(2, 4),
      modelo: chave.slice(20, 22),
    };
  }

  function nf(chave) { return dadosChave(chave).numero; }

  async function api(url, opts) {
    const resp = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, opts || {}));
    if (resp.status === 401) { window.location.href = "/login"; throw new Error("Sessão expirada."); }
    const body = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, body };
  }

  // -------------------------------------------------------- conversa ----

  function rolar() { log.lastElementChild && log.lastElementChild.scrollIntoView({ behavior: "smooth", block: "nearest" }); }

  function bot(html) {
    const msg = document.createElement("div");
    msg.className = "cv-msg cv-bot";
    msg.innerHTML = '<div class="cv-avatar" aria-hidden="true">🤖</div><div class="cv-bolha"><div class="cv-texto">' + html + '</div><div class="cv-area"></div></div>';
    log.appendChild(msg);
    rolar();
    return { msg, texto: msg.querySelector(".cv-texto"), area: msg.querySelector(".cv-area") };
  }

  function eu(texto) {
    const msg = document.createElement("div");
    msg.className = "cv-msg cv-eu";
    msg.innerHTML = '<div class="cv-bolha">' + esc(texto) + "</div>";
    log.appendChild(msg);
    rolar();
  }

  // Botões de resposta: ao clicar, somem e viram a fala do usuário.
  function opcoes(area, lista, classe) {
    const box = document.createElement("div");
    box.className = classe || "cv-botoes";
    lista.forEach(op => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = op.classe || "btn";
      b.innerHTML = op.html || esc(op.rotulo);
      b.addEventListener("click", () => {
        box.remove();
        if (op.fala !== null) eu(op.fala || op.rotulo);
        op.acao();
      });
      box.appendChild(b);
    });
    area.appendChild(box);
    return box;
  }

  function passo(n) {
    document.querySelectorAll(".cv-passo").forEach(li => {
      const p = Number(li.dataset.passo);
      li.classList.toggle("ativo", p === n);
      li.classList.toggle("feito", p < n);
    });
  }

  function pararEspera() {
    if (!espera) return;
    clearInterval(espera.timer);
    clearInterval(espera.relogio);
    espera = null;
  }

  // ---------------------------------------------------------- etapas ----

  function inicio() {
    pararEspera();
    estado = {};
    guardar(CHAVE_ATUAL, null);
    log.innerHTML = "";
    bot("Olá! 👋 Vou te ajudar a emitir um CT-e no GW. É só responder algumas perguntas.");
    perguntarTipo();
  }

  function perguntarTipo() {
    passo(1);
    const m = bot("Que tipo de CT-e você precisa emitir?");
    opcoes(m.area, TIPOS.map(t => ({
      classe: "cv-opcao",
      html: '<span class="ic">' + t.icone + "</span><span><b>" + esc(t.rotulo) + "</b><small>" + esc(t.explicacao) + "</small></span>",
      fala: t.icone + " " + t.rotulo,
      acao: () => { estado.tipo = t.valor; perguntarChave(); },
    })), "cv-tipos");
  }

  function perguntarChave() {
    passo(2);
    const m = bot("Qual é a <b>chave de acesso da NF-e</b>? Pode digitar ou bipar o código de barras da DANFE.");
    m.area.innerHTML =
      '<form class="cv-entrada cv-chave" autocomplete="off">' +
      '<input type="text" inputmode="numeric" maxlength="60" placeholder="44 dígitos" aria-label="Chave de acesso da NF-e">' +
      '<button type="submit" class="btn-primary" disabled>Continuar</button></form>' +
      '<div class="cv-dica">0 de 44 dígitos</div>';
    const form = m.area.querySelector("form");
    const input = form.querySelector("input");
    const btn = form.querySelector("button");
    const dica = m.area.querySelector(".cv-dica");
    let chaveOk = null;

    input.addEventListener("input", () => {
      const dig = input.value.replace(/\D/g, "");
      chaveOk = null;
      dica.className = "cv-dica";
      if (dig.length < 44) {
        dica.textContent = dig.length + " de 44 dígitos";
      } else if (dig.length > 44) {
        dica.textContent = "A chave tem 44 dígitos — essa tem " + dig.length + ".";
        dica.classList.add("erro");
      } else if (!dvConfere(dig)) {
        dica.textContent = "Essa chave não confere (dígito verificador). Algum número foi digitado errado?";
        dica.classList.add("erro");
      } else if (dadosChave(dig).modelo !== "55") {
        dica.textContent = "Essa não é a chave de uma NF-e (parece de CT-e). Use a chave da nota fiscal.";
        dica.classList.add("erro");
      } else {
        const d = dadosChave(dig);
        dica.textContent = "✓ NF " + d.numero + " · série " + d.serie + " · emitida em " + d.emissao;
        dica.classList.add("ok");
        chaveOk = dig;
      }
      btn.disabled = !chaveOk;
    });
    form.addEventListener("submit", e => {
      e.preventDefault();
      if (!chaveOk) return;
      estado.chave = chaveOk;
      m.area.innerHTML = "";
      eu("NF " + nf(chaveOk) + " (chave …" + chaveOk.slice(-6) + ")");
      perguntarNome();
    });
    input.focus();
  }

  function perguntarNome() {
    if (SOLICITANTE_FIXO) {
      estado.solicitante = SOLICITANTE_FIXO;
      criarPedido(false);
      return;
    }
    const m = bot("E quem está pedindo esta emissão? (fica registrado no pedido)");
    m.area.innerHTML =
      '<form class="cv-entrada" autocomplete="off">' +
      '<input type="text" maxlength="120" placeholder="Seu nome" aria-label="Seu nome">' +
      '<button type="submit" class="btn-primary">Buscar no GW</button></form>';
    const form = m.area.querySelector("form");
    const input = form.querySelector("input");
    input.value = ler(CHAVE_NOME) || "";
    form.addEventListener("submit", e => {
      e.preventDefault();
      const nome = input.value.trim();
      if (!nome) { input.focus(); return; }
      guardar(CHAVE_NOME, nome);
      estado.solicitante = nome;
      m.area.innerHTML = "";
      eu(nome);
      criarPedido(false);
    });
    input.focus();
    input.select();
  }

  async function criarPedido(confirmarRepetido) {
    const t = TIPO[estado.tipo];
    const r = await api("/api/emissoes", {
      method: "POST",
      body: JSON.stringify({ tipo: estado.tipo, chave: estado.chave, solicitante: estado.solicitante, confirmar_repetido: confirmarRepetido }),
    }).catch(e => ({ ok: false, body: { erro: e.message } }));

    if (r.status === 409 && r.body.em_andamento) {
      const em = r.body.em_andamento;
      bot("Já existe um pedido em andamento para essa nota (aberto por <b>" + esc(em.solicitante) + "</b> em " + fmtData(em.criado_em) + "). Vou continuar a partir dele.");
      acompanhar(em);
    } else if (r.status === 409 && r.body.ja_salvo) {
      const em = r.body.ja_salvo;
      const m = bot("⚠️ Atenção: já foi salvo um CT-e de <b>" + esc(t.rotulo.toLowerCase()) + "</b> para essa nota — o CT-e <b>" +
        esc(em.numero_ct) + "</b>, pedido por " + esc(em.solicitante) + " em " + fmtData(em.criado_em) + ". Quer emitir outro mesmo assim?");
      opcoes(m.area, [
        { rotulo: "Sim, emitir outro", classe: "btn-primary", acao: () => criarPedido(true) },
        { rotulo: "Não, começar de novo", acao: inicio },
      ]);
    } else if (!r.ok) {
      const m = bot("😕 Não consegui criar o pedido: " + msgErro(r.body.erro));
      opcoes(m.area, [
        { rotulo: "Tentar de novo", classe: "btn-primary", acao: () => criarPedido(confirmarRepetido) },
        { rotulo: "Começar de novo", acao: inicio },
      ]);
    } else {
      acompanhar(r.body);
    }
    carregarRecentes();
  }

  function acompanhar(em) {
    estado.emissao = em;
    estado.tipo = em.tipo;
    estado.chave = em.chave_nfe;
    guardar(CHAVE_ATUAL, String(em.id));
    reagir(em);
  }

  function reagir(em) {
    if (em.status === "preparando") return esperar(em, "Procurando no GW o CT-e dessa nota…");
    if (em.status === "salvando") return esperar(em, "Salvando o CT-e no GW…");
    if (em.status === "escolher") return perguntarOriginal(em);
    if (em.status === "conferir") return mostrarAchado(em);
    if (em.status === "salvo") return concluido(em);
    if (em.status === "erro") return falhou(em);
    guardar(CHAVE_ATUAL, null);
    const m = bot("Esse pedido foi cancelado. Nada foi salvo no GW.");
    opcoes(m.area, [{ rotulo: "Emitir outro CT-e", classe: "btn-primary", fala: null, acao: inicio }]);
  }

  function esperar(em, texto) {
    pararEspera();
    passo(em.status === "salvando" ? 4 : 3);
    const m = bot('<span class="cv-espera"><span class="cv-spinner" aria-hidden="true"></span><span>' + esc(texto) +
      '</span><span class="cv-tempo">0:00</span></span>' +
      '<div class="cv-dica">Costuma levar de 1 a 3 minutos. Pode usar o resto do painel enquanto isso — o pedido continua em “Pedidos recentes”.</div>');
    const tempo = m.texto.querySelector(".cv-tempo");
    const comeco = Date.now();
    const statusInicial = em.status;
    espera = {
      relogio: setInterval(() => { tempo.textContent = mmss(Math.round((Date.now() - comeco) / 1000)); }, 1000),
      timer: setInterval(async () => {
        const r = await api("/api/emissoes/" + em.id).catch(() => null);
        if (!r || !r.ok || r.body.status === statusInicial) return;
        pararEspera();
        m.texto.innerHTML = "✓ " + esc(texto.replace("…", "")) + ' <span class="cv-tempo">(' + mmss(Math.round((Date.now() - comeco) / 1000)) + ")</span>";
        estado.emissao = r.body;
        reagir(r.body);
        carregarRecentes();
      }, 5000),
    };
  }

  function perguntarOriginal(em) {
    passo(3);
    const m = bot("Essa nota aparece em <b>" + em.opcoes.length + " CT-e</b> no GW. Qual deles é o original desta " +
      esc(em.tipo_rotulo.toLowerCase()) + "?");
    opcoes(m.area, em.opcoes.map(o => ({
      classe: "cv-opcao",
      html: '<span class="ic">📄</span><span><b>CT-e ' + esc(o.numero) + " · série " + esc(o.serie) + "</b><small>" +
        esc(o.emissao) + " · " + esc(o.filial) + " · " + moeda(o.total) + "<br>" + esc(o.remetente) + " → " + esc(o.destinatario) + "</small></span>",
      fala: "CT-e " + o.numero,
      acao: async () => {
        const r = await api("/api/emissoes/" + em.id + "/escolher", { method: "POST", body: JSON.stringify({ cte_id: o.id }) })
          .catch(e => ({ ok: false, body: { erro: e.message } }));
        if (!r.ok) {
          const e = bot("😕 Não consegui seguir: " + msgErro(r.body.erro));
          opcoes(e.area, [{ rotulo: "Começar de novo", classe: "btn-primary", fala: null, acao: inicio }]);
          return;
        }
        acompanhar(r.body);
      },
    })), "cv-tipos");
  }

  function fichaHtml(linhas) {
    return '<dl class="cv-ficha">' + linhas.filter(l => l[1] != null && l[1] !== "")
      .map(l => "<dt>" + l[0] + "</dt><dd>" + l[1] + "</dd>").join("") + "</dl>";
  }

  function mostrarAchado(em) {
    passo(3);
    const r = em.resumo || {};
    const nota = em.tipo === "devolucao" ? '<div class="cv-dica">Como é devolução, o GW já inverteu remetente e destinatário.</div>' : "";
    bot("Achei! É o CT-e <b>" + esc(r.cte_original) + "</b> de " + esc(r.emissao_original) + " (" + moeda(r.total_original) + "):" +
      fichaHtml([["Remetente", esc(r.remetente)], ["Destinatário", esc(r.destinatario)], ["Quem paga", esc(r.tomador)]]) + nota);
    perguntarFrete(em);
  }

  function perguntarFrete(em) {
    const r = em.resumo || {};
    if (em.frete_calculado != null) {
      const m = bot("Pela tabela do cliente, o frete fica <b>" + moeda(em.frete_calculado) + "</b> <span class=\"cv-sutil\">— " +
        esc(r.origem_frete) + "</span>. Posso usar esse valor?");
      opcoes(m.area, [
        { rotulo: "Sim, usar " + moeda(em.frete_calculado), classe: "btn-primary", acao: () => confirmarSalvar(em, em.frete_calculado) },
        { rotulo: "Informar outro valor", acao: () => perguntarValor(em, "Qual o valor do frete?") },
      ]);
    } else {
      perguntarValor(em, "O GW não tem o valor do frete para " + esc(em.tipo_rotulo.toLowerCase()) +
        ' <span class="cv-sutil">— ' + esc(r.origem_frete) + "</span>. <b>Quanto vai ser cobrado?</b>");
    }
  }

  function perguntarValor(em, pergunta) {
    const m = bot(pergunta);
    m.area.innerHTML =
      '<form class="cv-entrada cv-valor" autocomplete="off"><span class="cv-rs">R$</span>' +
      '<input type="text" inputmode="decimal" placeholder="0,00" aria-label="Valor do frete">' +
      '<button type="submit" class="btn-primary">Continuar</button></form><div class="cv-dica"></div>';
    const form = m.area.querySelector("form");
    const input = form.querySelector("input");
    const dica = m.area.querySelector(".cv-dica");
    form.addEventListener("submit", e => {
      e.preventDefault();
      const v = lerValor(input.value);
      if (!(v > 0)) {
        dica.textContent = "Digite um valor maior que zero, por exemplo 150,00.";
        dica.className = "cv-dica erro";
        input.focus();
        return;
      }
      m.area.innerHTML = "";
      eu(moeda(v));
      confirmarSalvar(em, v);
    });
    input.focus();
  }

  function confirmarSalvar(em, frete) {
    passo(4);
    const r = em.resumo || {};
    const t = TIPO[em.tipo];
    const m = bot("Tudo certo! Confira o resumo antes de salvar:" + fichaHtml([
      ["Tipo", t.icone + " " + esc(t.rotulo)],
      ["Nota fiscal", "NF " + esc(nf(em.chave_nfe))],
      ["CT-e original", esc(r.cte_original) + " de " + esc(r.emissao_original)],
      ["Trajeto", esc(r.remetente) + " → " + esc(r.destinatario)],
      ["Quem paga", esc(r.tomador)],
      ["Frete", "<b>" + moeda(frete) + "</b>"],
    ]) + '<p class="cv-p">Posso salvar no GW? Ele fica salvo <b>sem transmitir</b> — a transmissão continua sendo feita por você no GW.</p>');
    opcoes(m.area, [
      {
        rotulo: "💾 Salvar no GW", classe: "btn-primary", fala: "Pode salvar",
        acao: async () => {
          const res = await api("/api/emissoes/" + em.id + "/salvar", { method: "POST", body: JSON.stringify({ frete: frete }) })
            .catch(e => ({ ok: false, body: { erro: e.message } }));
          if (!res.ok) {
            const e = bot("😕 Não consegui pedir o salvamento: " + msgErro(res.body.erro));
            opcoes(e.area, [{ rotulo: "Começar de novo", classe: "btn-primary", fala: null, acao: inicio }]);
            return;
          }
          acompanhar(res.body);
          carregarRecentes();
        },
      },
      { rotulo: "Trocar o valor do frete", acao: () => perguntarValor(em, "Qual o valor do frete?") },
      {
        rotulo: "Cancelar pedido", fala: "Cancelar",
        acao: async () => {
          await api("/api/emissoes/" + em.id + "/cancelar", { method: "POST" }).catch(() => null);
          guardar(CHAVE_ATUAL, null);
          const c = bot("Pedido cancelado. Nada foi salvo no GW.");
          opcoes(c.area, [{ rotulo: "Emitir outro CT-e", classe: "btn-primary", fala: null, acao: inicio }]);
          carregarRecentes();
        },
      },
    ]);
  }

  function concluido(em) {
    passo(5);
    guardar(CHAVE_ATUAL, null);
    const total = em.total_prestacao && Math.abs(em.total_prestacao - em.frete_final) >= 0.01
      ? " (total da prestação com impostos: " + moeda(em.total_prestacao) + ")" : "";
    const m = bot("✅ Pronto! O CT-e <b>" + esc(em.numero_ct) + "</b> foi salvo no GW com frete " + moeda(em.frete_final) + total + "." +
      '<p class="cv-p">Agora é só abrir a tela de <b>Conhecimentos</b> no GW e <b>transmitir</b>.</p>');
    opcoes(m.area, [{ rotulo: "Emitir outro CT-e", classe: "btn-primary", fala: null, acao: inicio }]);
  }

  function falhou(em) {
    guardar(CHAVE_ATUAL, null);
    const m = bot("😕 Não consegui concluir: " + msgErro(em.erro_mensagem || "erro sem detalhe"));
    const lista = [];
    if (SOLICITANTE_FIXO || estado.solicitante || ler(CHAVE_NOME)) {
      lista.push({
        rotulo: "Tentar de novo", classe: "btn-primary",
        acao: () => {
          estado.solicitante = SOLICITANTE_FIXO || estado.solicitante || ler(CHAVE_NOME);
          criarPedido(false);
        },
      });
    }
    lista.push({ rotulo: "Começar de novo", fala: null, acao: inicio });
    opcoes(m.area, lista);
  }

  function retomar(em) {
    pararEspera();
    log.innerHTML = "";
    estado = { solicitante: SOLICITANTE_FIXO || ler(CHAVE_NOME) };
    const t = TIPO[em.tipo] || { icone: "", rotulo: em.tipo };
    bot("Continuando o pedido de <b>" + esc(em.solicitante) + "</b> de " + fmtData(em.criado_em) + ":");
    eu(t.icone + " " + t.rotulo + " · NF " + nf(em.chave_nfe));
    acompanhar(em);
  }

  // ------------------------------------------------- pedidos recentes ----

  async function carregarRecentes() {
    const r = await api("/api/emissoes").catch(() => null);
    if (!r || !r.ok) return;
    const lista = r.body.emissoes || [];
    document.getElementById("cv-count").textContent = lista.length ? lista.length + " pedido(s)" : "";
    if (!lista.length) {
      recentesBody.innerHTML = '<tr><td colspan="6"><div class="empty-state">Nenhum pedido ainda.</div></td></tr>';
      return;
    }
    recentesBody.innerHTML = lista.map(em => {
      const t = TIPO[em.tipo] || { icone: "", rotulo: em.tipo };
      const sit = STATUS_LABEL[em.status] || em.status;
      const detalhe = em.status === "salvo" ? " · CT-e " + esc(em.numero_ct) : "";
      return "<tr>" +
        "<td>" + fmtData(em.criado_em) + "</td>" +
        "<td>" + esc(em.solicitante) + "</td>" +
        "<td>" + t.icone + " " + esc(t.rotulo) + "</td>" +
        '<td title="' + esc(em.chave_nfe) + '">' + esc(nf(em.chave_nfe)) + "</td>" +
        '<td class="' + (em.status === "erro" ? "flag-no" : "") + '"' + (em.erro_mensagem ? ' title="' + esc(em.erro_mensagem) + '"' : "") + ">" +
        esc(sit) + detalhe + "</td>" +
        "<td>" + (EM_ANDAMENTO.includes(em.status) || em.status === "salvo" || em.status === "erro"
          ? '<button type="button" class="btn" data-abrir="' + em.id + '">Abrir</button>' : "") + "</td>" +
        "</tr>";
    }).join("");
    estado.recentes = lista;
  }

  recentesBody.addEventListener("click", e => {
    const btn = e.target.closest("button[data-abrir]");
    if (!btn) return;
    const em = (estado.recentes || []).find(x => String(x.id) === btn.dataset.abrir);
    if (!em) return;
    const recentes = estado.recentes;
    retomar(em);
    estado.recentes = recentes;
    document.querySelector(".cv-card").scrollIntoView({ behavior: "smooth" });
  });

  document.getElementById("cv-recomecar").addEventListener("click", inicio);

  // ------------------------------------------------------------ início ----

  (async function () {
    carregarRecentes();
    const atual = ler(CHAVE_ATUAL);
    if (atual) {
      const r = await api("/api/emissoes/" + atual).catch(() => null);
      if (r && r.ok && EM_ANDAMENTO.includes(r.body.status)) {
        retomar(r.body);
        return;
      }
    }
    inicio();
  })();
})();
