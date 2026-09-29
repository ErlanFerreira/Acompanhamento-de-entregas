(function () {
  "use strict";

  // O leitor de código de barras USB funciona como um teclado: "digita" os
  // 44 dígitos e manda Enter. Cada Enter no campo = uma leitura.
  const fLeitura = document.getElementById("f-leitura");
  const feedback = document.getElementById("bip-feedback");
  const tbody = document.getElementById("bip-body");
  const vazio = document.getElementById("bip-vazio");
  const count = document.getElementById("bip-count");
  const btnExportar = document.getElementById("btn-exportar");
  const btnLimpar = document.getElementById("btn-limpar");
  const btnProtocolo = document.getElementById("btn-protocolo");
  const btnControle = document.getElementById("btn-controle");
  const fTomador = document.getElementById("f-tomador");
  const fMes = document.getElementById("f-mes");
  const fResponsavel = document.getElementById("f-responsavel");

  const STATUS_LABEL = { PE: "Pendente", DP: "Entregue no prazo", FPE: "Entregue fora do prazo" };
  const FINALIDADE_LABEL = { comprovante: "comprovantes", fatura: "faturas" };

  // Cada finalidade tem sua própria lista na tela (guardada no navegador
  // pra não perder a conferência se a página recarregar). O registro que
  // vale para os relatórios fica no servidor.
  let finalidade = lerStorage("bipagem:finalidade") || "comprovante";
  let itens = [];

  function lerStorage(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function gravarStorage(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sem storage */ } }
  function chaveLista() { return "bipagem:itens:" + finalidade; }
  function carregar() {
    try { return JSON.parse(lerStorage(chaveLista())) || []; } catch (e) { return []; }
  }
  function salvar() { gravarStorage(chaveLista(), JSON.stringify(itens)); }

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function vazioSe(v) { return v ? esc(v) : '<span class="bip-muted">—</span>'; }

  function mostrarFeedback(tipo, html) {
    feedback.className = "bip-feedback show " + tipo;
    feedback.innerHTML = html;
  }

  function selecionarFinalidade(f) {
    finalidade = f;
    gravarStorage("bipagem:finalidade", f);
    document.querySelectorAll(".bip-finalidade").forEach(function (b) {
      const ativo = b.dataset.finalidade === f;
      b.classList.toggle("active", ativo);
      b.setAttribute("aria-checked", ativo ? "true" : "false");
    });
    document.querySelectorAll(".bip-relatorio").forEach(function (el) {
      el.hidden = el.dataset.para !== f;
    });
    itens = carregar();
    feedback.className = "bip-feedback";
    render();
    fLeitura.focus();
  }

  function render() {
    tbody.innerHTML = itens.map(function (r, i) {
      const n = itens.length - i;
      const status = r.encontrado
        ? esc(STATUS_LABEL[r.status] || r.status || "—")
        : '<span class="bip-muted">Não está na base</span>';
      return (
        '<tr class="' + (r.encontrado ? "" : "bip-row-warn") + '">' +
          '<td class="num">' + n + '</td>' +
          '<td class="num"><strong>' + esc(r.numero) + '</strong>' +
            (r.tipo && r.tipo !== "CT-e" ? ' <span class="bip-tag">' + esc(r.tipo) + '</span>' : "") + '</td>' +
          '<td class="num">' + esc(r.serie) + '</td>' +
          '<td class="num">' + vazioSe(r.notas_fiscais) + '</td>' +
          '<td class="num">' + vazioSe(r.cte_redespacho) + '</td>' +
          '<td>' + vazioSe(r.consignatario) + '</td>' +
          '<td>' + vazioSe(r.destinatario) + '</td>' +
          '<td>' + status + '</td>' +
          '<td><button class="btn bip-remover" type="button" data-chave="' + esc(r.chave) + '" title="Desfazer este bip">✕</button></td>' +
        '</tr>'
      );
    }).join("");
    vazio.style.display = itens.length ? "none" : "";
    count.textContent = itens.length ? itens.length + " CT-e" : "";
    btnExportar.disabled = btnLimpar.disabled = btnProtocolo.disabled = !itens.length;
  }

  async function processar(leitura) {
    mostrarFeedback("info", "Consultando…");
    let r;
    try {
      const resp = await fetch("/api/bipagem", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ leitura: leitura, finalidade: finalidade }),
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      r = await resp.json();
    } catch (e) {
      mostrarFeedback("erro", "Falha na consulta (" + esc(e.message) + "). Bipe de novo.");
      return;
    }

    if (!r.ok) {
      mostrarFeedback("erro", esc(r.erro));
      return;
    }

    if (r.cnpj_consignatario && !fTomador.value) {
      const raiz = r.cnpj_consignatario.slice(0, 8);
      if (fTomador.querySelector('option[value="' + raiz + '"]')) fTomador.value = raiz;
    }

    const naTela = itens.some(function (x) { return x.chave === r.chave; });
    if (!naTela) {
      itens.unshift(r);
      salvar();
      render();
    }
    if (r.duplicado) {
      const quando = new Date(r.bipado_em).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
      mostrarFeedback("aviso", esc(r.tipo || "CT-e") + " <strong>" + esc(r.numero) + "</strong> já tinha sido bipado em " + FINALIDADE_LABEL[finalidade] + " (" + esc(quando) + ").");
      return;
    }

    const partes = [esc(r.tipo || "CT-e") + " <strong>" + esc(r.numero) + "</strong> (série " + esc(r.serie) + ")"];
    if (r.encontrado) {
      partes.push("NF: <strong>" + (r.notas_fiscais ? esc(r.notas_fiscais) : "—") + "</strong>");
      if (r.cte_redespacho) partes.push("CT-e parceiro: <strong>" + esc(r.cte_redespacho) + "</strong>");
      if (r.consignatario) partes.push("Tomador: " + esc(r.consignatario));
      mostrarFeedback("ok", partes.join(" · "));
    } else {
      partes.push("não está na base sincronizada (emissão " + esc(r.emissao_aamm) + ", emitente " + esc(r.cnpj_emitente_fmt) + ")");
      mostrarFeedback("aviso", partes.join(" · "));
    }
  }

  let timerAuto = null;

  function enviarCampo() {
    clearTimeout(timerAuto);
    const leitura = fLeitura.value.trim();
    fLeitura.value = "";
    if (leitura) processar(leitura);
  }

  fLeitura.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" && ev.key !== "Tab") return;
    ev.preventDefault();
    enviarCampo();
  });

  // Leitor configurado sem Enter/Tab no final: quando o campo já tem uma
  // chave completa (44 dígitos, ou a URL do QR Code com chCTe=) e o leitor
  // parou de "digitar", processa sozinho.
  fLeitura.addEventListener("input", function () {
    clearTimeout(timerAuto);
    const v = fLeitura.value.trim();
    if (/^\d{44}$/.test(v) || /chCTe=\d{44}/i.test(v)) timerAuto = setTimeout(enviarCampo, 300);
  });

  // Mantém o foco no campo -- quem bipa em sequência não quer ter que
  // clicar de novo depois de cada leitura.
  document.addEventListener("click", function (ev) {
    if (!ev.target.closest("button, a, input, select, textarea, label")) fLeitura.focus();
  });

  document.querySelectorAll(".bip-finalidade").forEach(function (b) {
    b.addEventListener("click", function () { selecionarFinalidade(b.dataset.finalidade); });
  });

  tbody.addEventListener("click", async function (ev) {
    const btn = ev.target.closest(".bip-remover");
    if (!btn) return;
    const chave = btn.dataset.chave;
    try {
      const resp = await fetch("/api/bipagem?chave=" + encodeURIComponent(chave) + "&finalidade=" + finalidade, {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
    } catch (e) {
      mostrarFeedback("erro", "Não consegui desfazer o bip (" + esc(e.message) + ").");
      return;
    }
    itens = itens.filter(function (x) { return x.chave !== chave; });
    salvar();
    render();
    fLeitura.focus();
  });

  btnLimpar.addEventListener("click", function () {
    // Só limpa a tela -- os bips continuam registrados (o controle de
    // comprovantes continua mostrando esses CT-e como "Recebido").
    if (!confirm("Limpar os " + itens.length + " CT-e da tela? Os bips continuam registrados.")) return;
    itens = [];
    salvar();
    render();
    feedback.className = "bip-feedback";
    fLeitura.focus();
  });

  async function baixar(url, opcoes, nomePadrao, botao) {
    botao.disabled = true;
    try {
      const resp = await fetch(url, Object.assign({ credentials: "same-origin" }, opcoes));
      if (!resp.ok) {
        let msg = "HTTP " + resp.status;
        try { msg = (await resp.json()).erro || msg; } catch (e) { /* resposta não-JSON */ }
        throw new Error(msg);
      }
      const nome = ((resp.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/) || [])[1] || nomePadrao;
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = nome;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      mostrarFeedback("erro", "Falha ao gerar o arquivo: " + esc(e.message));
    } finally {
      botao.disabled = false;
      render();
    }
  }

  function chavesEmOrdem() {
    // Reconsulta no servidor em vez de usar o que está na tela -- se uma
    // sincronização rodou no meio da conferência, sai o dado atual.
    return itens.slice().reverse().map(function (x) { return x.chave; });
  }

  function postJson(corpo) {
    return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo) };
  }

  btnExportar.addEventListener("click", function () {
    baixar("/api/bipagem/exportar", postJson({ leituras: chavesEmOrdem() }), "bipagem.xlsx", btnExportar);
  });

  btnProtocolo.addEventListener("click", function () {
    baixar("/api/bipagem/protocolo", postJson({ leituras: chavesEmOrdem() }), "protocolo_faturas.xlsx", btnProtocolo);
  });

  btnControle.addEventListener("click", function () {
    if (!fTomador.value || !fMes.value) {
      mostrarFeedback("erro", "Escolha o tomador e o mês do controle.");
      return;
    }
    gravarStorage("bipagem:responsavel", fResponsavel.value.trim());
    const qs = new URLSearchParams({ tomador: fTomador.value, mes: fMes.value, responsavel: fResponsavel.value.trim() });
    baixar("/api/bipagem/controle?" + qs.toString(), {}, "controle_comprovantes.xlsx", btnControle);
  });

  async function carregarTomadores() {
    try {
      const resp = await fetch("/api/bipagem/tomadores", { credentials: "same-origin" });
      const body = await resp.json();
      const lista = body.tomadores || [];
      if (!lista.length) {
        fTomador.innerHTML = '<option value="">Nenhum tomador na base (aguarde a próxima sincronização)</option>';
        return;
      }
      fTomador.innerHTML = '<option value="">Escolha…</option>' + lista.map(function (t) {
        return '<option value="' + esc(t.cnpj_raiz) + '">' + esc(t.nome) + " (" + esc(t.cnpj_raiz) + ")</option>";
      }).join("");
      // Pré-seleciona o tomador mais frequente entre os bipados na tela.
      const freq = {};
      itens.forEach(function (x) { if (x.cnpj_consignatario) { const k = x.cnpj_consignatario.slice(0, 8); freq[k] = (freq[k] || 0) + 1; } });
      const top = Object.keys(freq).sort(function (a, b) { return freq[b] - freq[a]; })[0];
      if (top && lista.some(function (t) { return t.cnpj_raiz === top; })) fTomador.value = top;
    } catch (e) {
      fTomador.innerHTML = '<option value="">Falha ao carregar tomadores</option>';
    }
  }

  const hoje = new Date();
  fMes.value = hoje.getFullYear() + "-" + String(hoje.getMonth() + 1).padStart(2, "0");
  fResponsavel.value = lerStorage("bipagem:responsavel") || "";

  selecionarFinalidade(finalidade === "fatura" ? "fatura" : "comprovante");
  carregarTomadores();
})();
