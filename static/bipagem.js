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
  // Lista da tela (guardada no navegador pra não perder a conferência se a
  // página recarregar). O registro que vale para os relatórios fica no
  // servidor -- o mesmo bip serve pro controle e pro protocolo.
  const STORAGE_KEY = "bipagem:itens";
  let itens = [];

  function lerStorage(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function gravarStorage(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sem storage */ } }
  function lerLista(k) {
    try { return JSON.parse(lerStorage(k)) || []; } catch (e) { return []; }
  }
  function carregar() {
    // Junta as listas antigas, de quando comprovantes e faturas eram
    // bipados separadamente.
    const lista = lerLista(STORAGE_KEY);
    ["bipagem:itens:comprovante", "bipagem:itens:fatura"].forEach(function (k) {
      lerLista(k).forEach(function (x) {
        if (!lista.some(function (y) { return y.chave === x.chave; })) lista.push(x);
      });
      try { localStorage.removeItem(k); } catch (e) { /* sem storage */ }
    });
    try { localStorage.removeItem("bipagem:finalidade"); } catch (e) { /* sem storage */ }
    return lista;
  }
  function salvar() { gravarStorage(STORAGE_KEY, JSON.stringify(itens)); }

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function vazioSe(v) { return v ? esc(v) : '<span class="bip-muted">—</span>'; }

  function mostrarFeedback(tipo, html) {
    feedback.className = "bip-feedback show " + tipo;
    feedback.innerHTML = html;
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
        body: JSON.stringify({ leitura: leitura }),
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

    if (!fTomador.value) selecionarTomador(idTomador(r));

    // Bipar de novo um que já está na tela atualiza a linha com os dados
    // atuais do banco (ex: NF/parceiro que chegaram numa sincronização
    // depois do primeiro bip).
    const pos = itens.findIndex(function (x) { return x.chave === r.chave; });
    if (pos >= 0) itens[pos] = r;
    else itens.unshift(r);
    salvar();
    render();
    if (r.duplicado) {
      const quando = new Date(r.bipado_em).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
      mostrarFeedback("aviso", esc(r.tipo || "CT-e") + " <strong>" + esc(r.numero) + "</strong> já tinha sido bipado (" + esc(quando) + ").");
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

  tbody.addEventListener("click", async function (ev) {
    const btn = ev.target.closest(".bip-remover");
    if (!btn) return;
    const chave = btn.dataset.chave;
    try {
      const resp = await fetch("/api/bipagem?chave=" + encodeURIComponent(chave), {
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

  // Mesmo `id` de /api/bipagem/tomadores: raiz do CNPJ ou "nome:<nome>"
  // (CT-e sincronizado antes de existir a coluna de CNPJ do tomador).
  function idTomador(item) {
    if (item.cnpj_consignatario) return item.cnpj_consignatario.slice(0, 8);
    return item.consignatario ? "nome:" + item.consignatario.trim() : null;
  }

  function selecionarTomador(id) {
    if (id && Array.prototype.some.call(fTomador.options, function (o) { return o.value === id; })) fTomador.value = id;
  }

  async function carregarTomadores() {
    try {
      const resp = await fetch("/api/bipagem/tomadores", { credentials: "same-origin" });
      if (!resp.ok) {
        // Não mascara erro (sessão, servidor) como "base vazia".
        fTomador.innerHTML = '<option value="">Falha ao carregar tomadores (HTTP ' + resp.status + ')</option>';
        return;
      }
      const lista = (await resp.json()).tomadores || [];
      if (!lista.length) {
        fTomador.innerHTML = '<option value="">Nenhum CT-e na base ainda</option>';
        return;
      }
      fTomador.innerHTML = '<option value="">Escolha…</option>' + lista.map(function (t) {
        const rotulo = t.cnpj_raiz ? t.nome + " (" + t.cnpj_raiz + ")" : t.nome;
        return '<option value="' + esc(t.id) + '">' + esc(rotulo) + "</option>";
      }).join("");
      // Pré-seleciona o tomador mais frequente entre os bipados na tela.
      const freq = {};
      itens.forEach(function (x) { const k = idTomador(x); if (k) freq[k] = (freq[k] || 0) + 1; });
      const top = Object.keys(freq).sort(function (a, b) { return freq[b] - freq[a]; })[0];
      if (top) selecionarTomador(top);
    } catch (e) {
      fTomador.innerHTML = '<option value="">Falha ao carregar tomadores</option>';
    }
  }

  const hoje = new Date();
  fMes.value = hoje.getFullYear() + "-" + String(hoje.getMonth() + 1).padStart(2, "0");
  fResponsavel.value = lerStorage("bipagem:responsavel") || "";

  itens = carregar();
  salvar();
  render();
  fLeitura.focus();
  carregarTomadores();
})();
