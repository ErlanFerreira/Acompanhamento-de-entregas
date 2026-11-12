(function () {
  "use strict";

  // Agendamento de entregas por WhatsApp (fase 1): importa o XML das NFs (o
  // servidor acha o telefone na observação e junta com o CT-e/previsão), mostra
  // um card por NF e o botão "Enviar no WhatsApp" abre o link wa.me com a
  // mensagem pronta -- a analista confere e envia. A resposta é marcada aqui.

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function fmtN(n) { return Number(n || 0).toLocaleString("pt-BR"); }
  function fmtData(iso) { return iso ? iso.slice(0, 10).split("-").reverse().join("/") : ""; }
  function fmtDataHora(iso) { return iso ? fmtData(iso) + " " + iso.slice(11, 16) : ""; }
  function fmtFone(f) {
    if (!f) return "";
    const d = f.replace(/^55/, "");
    return "(" + d.slice(0, 2) + ") " + (d.length === 11 ? d.slice(2, 7) + "-" + d.slice(7) : d.slice(2, 6) + "-" + d.slice(6));
  }
  const DIAS = ["domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado"];
  function diaSemana(iso) { if (!iso) return ""; const [a, m, d] = iso.split("-").map(Number); return DIAS[new Date(a, m - 1, d).getDay()]; }
  function hojeIso() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  // "JOAO DA SILVA LTDA" -> "Joao da Silva LTDA"
  function nomeBonito(s) {
    const minusc = new Set(["da", "de", "do", "das", "dos", "e"]);
    const siglas = new Set(["LTDA", "ME", "EPP", "EIRELI", "SA", "S/A", "S.A.", "MEI", "CIA"]);
    return String(s || "").trim().split(/\s+/).map((p, i) => {
      if (siglas.has(p.toUpperCase())) return p.toUpperCase();
      const l = p.toLowerCase();
      return i > 0 && minusc.has(l) ? l : l.charAt(0).toUpperCase() + l.slice(1);
    }).join(" ");
  }

  const $ = id => document.getElementById(id);
  const raiz = document.querySelector(".viz-root");
  const ANALISTA = (raiz.dataset.analista || "").trim().split(/\s+/)[0] || "";

  const MODELO_PADRAO =
    "Olá, {cliente}! Tudo bem?\n" +
    "Aqui é {analista}, da *Transrota*, transportadora responsável pela entrega do seu pedido *Fotus* (NF {nf}).\n\n" +
    "A entrega está prevista para *{data} ({dia_semana})*, em {cidade}.\n\n" +
    "Podemos confirmar? Caso não tenha ninguém para receber nesse dia, por favor nos informe a melhor data.";
  const CHAVE_MODELO = "agendamento.modelo";

  function lerModelo() { try { return localStorage.getItem(CHAVE_MODELO) || MODELO_PADRAO; } catch (e) { return MODELO_PADRAO; } }
  function salvarModelo(v) { try { if (v && v !== MODELO_PADRAO) localStorage.setItem(CHAVE_MODELO, v); else localStorage.removeItem(CHAVE_MODELO); } catch (e) { /* sem storage */ } }

  const SITUACAO = {
    a_enviar: ["A enviar", "neutro"],
    aguardando: ["Aguardando resposta", "aviso"],
    confirmado: ["✓ Confirmado", "ok"],
    reagendar: ["Pediu outra data", "serio"],
    sem_contato: ["Sem contato", "critico"],
    concluido: ["Concluído", "ok"],
  };
  const HORAS_SEM_RESPOSTA = 24;

  let itens = [];
  let editando = null;      // agendamento em edição (null = novo)
  let reagendando = null;   // agendamento do modal de nova data

  async function api(url, opts) {
    const resp = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, opts || {}));
    if (resp.status === 401) { window.location.href = "/login"; throw new Error("Sessão expirada."); }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.erro || body.detail || "Falha na operação.");
    return body;
  }

  function erro(id, texto) { const el = $(id); el.textContent = texto || ""; el.classList.toggle("show", !!texto); }

  function aviso(texto, tipo) {
    const el = $("ag-aviso");
    el.className = "bip-feedback show " + (tipo || "");
    el.innerHTML = texto;
    clearTimeout(aviso.t);
    if (tipo !== "erro") aviso.t = setTimeout(() => { el.className = "bip-feedback"; }, 12000);
  }

  // ------------------------------------------------------- mensagem ----

  function dataDoAgendamento(a) { return a.data_combinada || a.data_prevista || (a.carga && a.carga.previsao) || ""; }

  function mensagem(a) {
    const data = dataDoAgendamento(a);
    const valores = {
      cliente: nomeBonito(a.cliente) || "cliente",
      nf: a.nf, cte: a.cte || "",
      data: data ? fmtData(data) : "(data a combinar)",
      dia_semana: diaSemana(data),
      cidade: nomeBonito(a.cidade) || "seu endereço",
      analista: ANALISTA || "a equipe de entregas",
    };
    return lerModelo().replace(/\{(\w+)\}/g, (m, k) => (k in valores ? valores[k] : m)).replace(/ \(\)/g, "");
  }

  function linkWhatsApp(a) { return "https://wa.me/" + a.telefone + "?text=" + encodeURIComponent(mensagem(a)); }

  // ----------------------------------------------------------- lista ----

  let seq = 0;
  async function carregar() {
    const meu = ++seq;
    const p = new URLSearchParams({ situacao: $("f-situacao").value });
    const busca = $("f-busca").value.trim();
    if (busca) p.set("busca", busca);
    try {
      const r = await api("/api/agendamento?" + p.toString());
      if (meu !== seq) return;
      itens = r.itens;
      Object.keys(r.contagem).forEach(s => { const el = $("k-" + s); if (el) el.textContent = fmtN(r.contagem[s]); });
      render();
    } catch (e) {
      $("ag-cards").innerHTML = '<div class="empty-state">' + esc(e.message) + '</div>';
    }
  }

  function selo(texto, tipo) { return '<span class="gdc-selo ag-selo-' + tipo + '">' + texto + '</span>'; }

  function horasDesde(iso) { return iso ? (Date.now() - new Date(iso).getTime()) / 36e5 : 0; }

  function card(a, i) {
    const c = a.carga;
    const [nomeSit, tipoSit] = SITUACAO[a.situacao] || [a.situacao, "neutro"];
    const semResposta = a.situacao === "aguardando" && horasDesde(a.enviado_em) >= HORAS_SEM_RESPOSTA;
    const data = dataDoAgendamento(a);
    const atrasada = data && data < hojeIso() && !(c && c.entregue) && a.situacao !== "concluido";
    const selos =
      selo(nomeSit, tipoSit) +
      (semResposta ? selo("sem resposta há " + Math.floor(horasDesde(a.enviado_em)) + " h", "critico") : "") +
      (c && c.entregue ? selo("Entregue" + (c.data_entrega ? " em " + fmtData(c.data_entrega) : ""), "ok") : "") +
      (!a.id_cte ? selo("CT-e não encontrado", "neutro") : "") +
      (!a.telefone ? selo("sem telefone", "critico") : "");
    const outrosFones = (a.telefones || []).filter(f => f !== a.telefone);
    const meta = [
      a.cte ? '<span title="CT-e">🚚 CT-e ' + esc(a.cte) + '</span>' : "",
      a.cidade ? '<span title="Cidade">📍 ' + esc(nomeBonito(a.cidade)) + (a.uf ? "/" + esc(a.uf) : "") + '</span>' : "",
      '<span title="Data da entrega" class="' + (atrasada ? "ag-atrasada" : "") + '">📅 ' +
        (a.data_combinada ? "Combinada " + fmtData(a.data_combinada) + " (" + diaSemana(a.data_combinada) + ")"
          : data ? "Previsão " + fmtData(data) + " (" + diaSemana(data) + ")" : "Sem previsão") + '</span>',
      a.telefone ? '<span title="Telefone">📱 ' + esc(fmtFone(a.telefone)) + '</span>' : "",
      outrosFones.length ? '<span title="Outros telefones na observação da NF">☎ outros: ' +
        outrosFones.map(f => '<button type="button" class="ag-fone-alt" data-usar-fone="' + i + '" data-fone="' + f + '">' + esc(fmtFone(f)) + '</button>').join(" ") + '</span>' : "",
      a.envios ? '<span title="Último envio">✉ ' + (a.envios > 1 ? a.envios + " envios, último " : "Enviado ") + fmtDataHora(a.enviado_em) +
        (a.enviado_por ? " por " + esc(a.enviado_por) : "") + '</span>' : "",
    ].join("");
    const podeEnviar = !!a.telefone && a.situacao !== "concluido";
    const btnEnviar = podeEnviar
      ? '<button type="button" class="btn ag-btn-whats" data-enviar="' + i + '">💬 ' + (a.envios ? "Reenviar" : "Enviar") + ' no WhatsApp</button>'
      : "";
    const respostas = a.situacao === "concluido"
      ? '<button type="button" class="btn" data-sit="aguardando" data-i="' + i + '">↺ Reabrir</button>'
      : (a.situacao !== "confirmado" ? '<button type="button" class="btn ag-btn-ok" data-sit="confirmado" data-i="' + i + '">✓ Confirmou</button>' : "") +
        '<button type="button" class="btn" data-reagendar="' + i + '">📅 Outra data</button>' +
        (a.situacao !== "sem_contato" ? '<button type="button" class="btn" data-sit="sem_contato" data-i="' + i + '">✗ Sem contato</button>' : "") +
        '<button type="button" class="btn" data-sit="concluido" data-i="' + i + '" title="Sai da lista (entrega feita)">Concluir</button>';
    const obs = a.observacao_nf ? '<details class="ag-obs"><summary>Observação da NF</summary><div>' + esc(a.observacao_nf) + '</div></details>' : "";
    const anot = a.anotacao ? '<div class="gdc-card-obs">💬 ' + esc(a.anotacao) + '</div>' : "";
    return '<article class="gdc-card">' +
      '<div class="gdc-card-topo"><h3>NF ' + esc(a.nf) + '</h3>' + selos + '</div>' +
      '<div class="gdc-card-dest" title="' + esc(a.cliente) + '">' + esc(a.cliente || "Cliente não informado") + '</div>' +
      '<div class="gdc-card-meta">' + meta + '</div>' +
      '<div class="gdc-card-status ag-acoes">' + btnEnviar + respostas +
        '<span class="filters-spacer"></span>' +
        '<button type="button" class="btn" data-editar="' + i + '">Editar</button>' +
        '<button type="button" class="btn gdc-btn-excluir" data-excluir="' + i + '">Excluir</button></div>' +
      anot + obs +
      '</article>';
  }

  function render() {
    $("ag-count").textContent = fmtN(itens.length) + " NF(s)";
    $("ag-cards").innerHTML = itens.length ? itens.map(card).join("")
      : '<div class="empty-state">Nada aqui. Importe o XML das NFs ou lance manualmente.</div>';
  }

  // Mostra a mudança na hora e recarrega (contagens; o item pode sair do filtro).
  function atualizarItem(i, novo) { itens[i] = novo; render(); carregar(); }

  $("ag-cards").addEventListener("click", async ev => {
    const b = ev.target.closest("button");
    if (!b) return;
    try {
      if (b.dataset.enviar != null) {
        const i = +b.dataset.enviar, a = itens[i];
        // Abre antes do await (senão o navegador bloqueia o popup).
        window.open(linkWhatsApp(a), "_blank", "noopener");
        atualizarItem(i, await api("/api/agendamento/" + a.id + "/enviado", { method: "POST" }));
      } else if (b.dataset.sit) {
        const i = +b.dataset.i;
        atualizarItem(i, await api("/api/agendamento/" + itens[i].id, { method: "PUT", body: JSON.stringify({ situacao: b.dataset.sit }) }));
      } else if (b.dataset.reagendar != null) {
        abrirReagendar(itens[+b.dataset.reagendar]);
      } else if (b.dataset.usarFone != null) {
        const i = +b.dataset.usarFone;
        atualizarItem(i, await api("/api/agendamento/" + itens[i].id, { method: "PUT", body: JSON.stringify({ telefone: b.dataset.fone }) }));
      } else if (b.dataset.editar != null) {
        abrirModal(itens[+b.dataset.editar]);
      } else if (b.dataset.excluir != null) {
        const a = itens[+b.dataset.excluir];
        if (!confirm("Excluir o agendamento da NF " + a.nf + "?")) return;
        await api("/api/agendamento/" + a.id, { method: "DELETE" });
        carregar(); carregarPendentes();
      }
    } catch (e) { aviso(esc(e.message), "erro"); }
  });

  // --------------------------------------------- CT-e sem agendamento ----

  let pendentes = [];
  async function carregarPendentes() {
    try {
      pendentes = (await api("/api/agendamento/sem-agendamento")).itens;
    } catch (e) {
      $("ag-pendentes-corpo").innerHTML = '<tr><td colspan="6" class="empty-state">' + esc(e.message) + '</td></tr>';
      return;
    }
    $("ag-pendentes-n").textContent = fmtN(pendentes.length);
    const hoje = hojeIso();
    $("ag-pendentes-corpo").innerHTML = pendentes.length ? pendentes.map((c, i) =>
      '<tr><td>' + esc(c.cte) + '</td><td>' + esc(c.nfs.join(", ") || "—") + '</td><td>' + esc(c.destinatario) + '</td>' +
      '<td>' + esc(c.cidade || "") + (c.uf ? "/" + esc(c.uf) : "") + '</td>' +
      '<td class="' + (c.previsao && c.previsao < hoje ? "ag-atrasada" : "") + '">' + fmtData(c.previsao) + '</td>' +
      '<td><button type="button" class="btn" data-lancar="' + i + '">Lançar</button></td></tr>').join("")
      : '<tr><td colspan="6" class="empty-state">Todos os CT-e pendentes da Fotus já têm agendamento. 🎉</td></tr>';
  }

  $("ag-pendentes-corpo").addEventListener("click", ev => {
    const b = ev.target.closest("[data-lancar]");
    if (!b) return;
    const c = pendentes[+b.dataset.lancar];
    abrirModal(null, { nf: c.nfs[0] || "", cliente: c.destinatario, cidade: c.cidade, data_prevista: c.previsao, cnpj_emitente: c.cnpj_remetente });
  });

  // ------------------------------------------------------ importar XML ----

  $("ag-xml").addEventListener("change", async ev => {
    const arquivos = Array.from(ev.target.files || []);
    ev.target.value = "";
    if (!arquivos.length) return;
    aviso("Lendo " + arquivos.length + " arquivo(s)…");
    try {
      const lidos = await Promise.all(arquivos.map(async f => ({ nome: f.name, conteudo: await f.text() })));
      const r = (await api("/api/agendamento/xml", { method: "POST", body: JSON.stringify({ arquivos: lidos }) })).resultado;
      const ok = r.filter(x => !x.erro), erros = r.filter(x => x.erro);
      const semFone = ok.filter(x => !x.telefone).map(x => x.nf);
      const semCte = ok.filter(x => !x.vinculado).map(x => x.nf);
      let msg = "<strong>" + ok.length + " NF(s) importada(s)</strong> (" + ok.filter(x => x.novo).length + " nova(s)).";
      if (semFone.length) msg += "<br>Sem telefone na observação: NF " + esc(semFone.join(", ")) + " — informe em Editar.";
      if (semCte.length) msg += "<br>CT-e ainda não sincronizado (sem previsão): NF " + esc(semCte.join(", ")) + ".";
      if (erros.length) msg += "<br>Não importados: " + erros.map(x => esc(x.arquivo + " — " + x.erro)).join("; ");
      aviso(msg, erros.length || semFone.length ? "aviso" : "ok");
      $("f-situacao").value = "andamento";
      carregar(); carregarPendentes();
    } catch (e) { aviso(esc(e.message), "erro"); }
  });

  // ------------------------------------------------------------ modal ----

  const modal = $("ag-modal");
  let tExtrair = null;

  function mostrarFones(lista) {
    $("a-fones").innerHTML = lista.length
      ? "Telefones na observação: " + lista.map(f => '<button type="button" class="ag-fone-alt" data-fone="' + f + '">' + esc(fmtFone(f)) + '</button>').join(" ") +
        ' <span class="ag-dica">(clique para usar)</span>'
      : ($("a-obs").value.trim() ? "Nenhum telefone encontrado na observação." : "");
  }

  $("a-obs").addEventListener("input", () => {
    clearTimeout(tExtrair);
    tExtrair = setTimeout(async () => {
      try {
        const r = await api("/api/agendamento/extrair", { method: "POST", body: JSON.stringify({ texto: $("a-obs").value }) });
        mostrarFones(r.telefones);
        if (!$("a-telefone").value.trim() && r.telefones.length) $("a-telefone").value = fmtFone(r.telefones[0]);
      } catch (e) { /* prévia: sem problema */ }
    }, 350);
  });
  $("a-fones").addEventListener("click", ev => {
    const b = ev.target.closest("[data-fone]");
    if (b) $("a-telefone").value = fmtFone(b.dataset.fone);
  });

  let prefill = {};
  function abrirModal(a, pre) {
    editando = a || null;
    prefill = pre || {};
    const v = a || prefill;
    $("ag-titulo").textContent = a ? "NF " + a.nf : "Lançar agendamento";
    $("a-nf").value = v.nf || "";
    $("a-nf").disabled = !!a;
    $("a-cliente").value = v.cliente || "";
    $("a-cidade").value = v.cidade || "";
    $("a-telefone").value = fmtFone(v.telefone || "");
    $("a-prevista").value = (v.data_prevista || "").slice(0, 10);
    $("a-obs").value = v.observacao_nf || "";
    $("a-anotacao").value = v.anotacao || "";
    $("a-resposta").hidden = !a;
    $("a-situacao").value = a ? a.situacao : "a_enviar";
    $("a-combinada").value = a && a.data_combinada ? a.data_combinada : "";
    mostrarFones(a ? a.telefones || [] : []);
    erro("ag-erro", "");
    modal.hidden = false;
    (a ? $("a-telefone") : $("a-nf").value ? $("a-obs") : $("a-nf")).focus();
  }
  function fecharModal() { modal.hidden = true; editando = null; }
  $("btn-novo").addEventListener("click", () => abrirModal(null));
  $("ag-fechar").addEventListener("click", fecharModal);
  $("ag-cancelar").addEventListener("click", fecharModal);

  $("ag-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const dados = {
      cliente: $("a-cliente").value, cidade: $("a-cidade").value, telefone: $("a-telefone").value,
      data_prevista: $("a-prevista").value || null, observacao_nf: $("a-obs").value, anotacao: $("a-anotacao").value,
    };
    try {
      if (editando) {
        dados.situacao = $("a-situacao").value;
        dados.data_combinada = $("a-combinada").value || null;
        await api("/api/agendamento/" + editando.id, { method: "PUT", body: JSON.stringify(dados) });
      } else {
        dados.nf = $("a-nf").value;
        dados.cnpj_emitente = prefill.cnpj_emitente || "";
        await api("/api/agendamento", { method: "POST", body: JSON.stringify(dados) });
      }
      fecharModal();
      carregar(); carregarPendentes();
    } catch (e) { erro("ag-erro", e.message); }
  });

  // ------------------------------------------------- outra data (modal) ----

  const modalData = $("ag-data-modal");
  function abrirReagendar(a) {
    reagendando = a;
    $("ag-data-titulo").textContent = "NF " + a.nf + " — cliente pediu outra data";
    $("ag-data-nova").value = a.data_combinada || "";
    $("ag-data-obs").value = a.anotacao || "";
    erro("ag-data-erro", "");
    modalData.hidden = false;
    $("ag-data-nova").focus();
  }
  $("ag-data-cancelar").addEventListener("click", () => { modalData.hidden = true; });
  $("ag-data-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    if (!$("ag-data-nova").value) { erro("ag-data-erro", "Informe a data."); return; }
    try {
      await api("/api/agendamento/" + reagendando.id, { method: "PUT", body: JSON.stringify({
        data_combinada: $("ag-data-nova").value, anotacao: $("ag-data-obs").value, situacao: "reagendar" }) });
      modalData.hidden = true;
      carregar();
    } catch (e) { erro("ag-data-erro", e.message); }
  });

  document.addEventListener("keydown", ev => {
    if (ev.key !== "Escape") return;
    if (!modal.hidden) fecharModal();
    if (!modalData.hidden) modalData.hidden = true;
  });

  // ---------------------------------------------------- filtros/modelo ----

  document.querySelectorAll(".ag-kpis [data-situacao]").forEach(b => b.addEventListener("click", () => {
    const sel = $("f-situacao");
    sel.value = sel.value === b.dataset.situacao ? "andamento" : b.dataset.situacao;
    carregar();
  }));
  $("f-situacao").addEventListener("change", carregar);
  let tBusca = null;
  $("f-busca").addEventListener("input", () => { clearTimeout(tBusca); tBusca = setTimeout(carregar, 300); });

  $("ag-modelo").value = lerModelo();
  $("ag-modelo").addEventListener("input", () => salvarModelo($("ag-modelo").value));
  $("ag-modelo-padrao").addEventListener("click", () => { $("ag-modelo").value = MODELO_PADRAO; salvarModelo(""); });

  carregar();
  carregarPendentes();
})();
