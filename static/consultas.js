(function () {
  "use strict";

  const fArquivo = document.getElementById("f-arquivo");
  const btnEnviar = document.getElementById("btn-enviar");
  const uploadStatus = document.getElementById("upload-status");

  let arquivoSelecionado = null;
  let colunaDetectada = null;

  function normalizar(s) {
    return String(s == null ? "" : s)
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().trim();
  }

  function detectarColunaNF(cabecalho) {
    for (const c of cabecalho) {
      const n = normalizar(c);
      if (n.includes("nota fiscal") || n.includes("nota") || n === "nf" || /\bnf\b/.test(n)) {
        return c;
      }
    }
    return null;
  }

  const STATUS_LABEL = {
    pendente: "Na fila",
    processando: "Processando",
    concluido: "Concluído",
    erro: "Erro",
  };

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

  fArquivo.addEventListener("change", function () {
    const file = fArquivo.files[0];
    if (!file) return;
    arquivoSelecionado = file;
    colunaDetectada = null;
    btnEnviar.disabled = true;
    uploadStatus.textContent = "Lendo cabeçalho da planilha…";

    const reader = new FileReader();
    reader.onload = function (e) {
      try {
        const data = new Uint8Array(e.target.result);
        const wb = XLSX.read(data, { type: "array" });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const rows = XLSX.utils.sheet_to_json(ws, { header: 1, range: 0 });
        const cabecalho = (rows[0] || []).map(c => String(c ?? "").trim()).filter(c => c);
        if (!cabecalho.length) {
          uploadStatus.textContent = "Não consegui ler o cabeçalho dessa planilha.";
          return;
        }
        const coluna = detectarColunaNF(cabecalho);
        if (!coluna) {
          uploadStatus.textContent = "Não encontrei uma coluna de nota fiscal nessa planilha.";
          return;
        }
        colunaDetectada = coluna;
        btnEnviar.disabled = false;
        uploadStatus.textContent = `Coluna de nota fiscal detectada: "${coluna}".`;
      } catch (err) {
        uploadStatus.textContent = "Erro ao ler o arquivo: " + err.message;
      }
    };
    reader.readAsArrayBuffer(file);
  });

  btnEnviar.addEventListener("click", async function () {
    if (!arquivoSelecionado || !colunaDetectada) return;
    btnEnviar.disabled = true;
    uploadStatus.textContent = "Enviando…";
    try {
      const fd = new FormData();
      fd.append("arquivo", arquivoSelecionado);
      fd.append("coluna_nf", colunaDetectada);
      const resp = await fetch("/api/consultas", { method: "POST", credentials: "same-origin", body: fd });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.erro || "Falha ao enviar.");
      uploadStatus.textContent = `Consulta enviada (${body.total_itens} NFs). Acompanhe abaixo -- isso pode levar de alguns minutos até cerca de 1 hora para planilhas grandes.`;
      fArquivo.value = "";
      arquivoSelecionado = null;
      colunaDetectada = null;
      loadJobs();
      garantirPolling();
    } catch (e) {
      uploadStatus.textContent = "Erro: " + e.message;
    } finally {
      btnEnviar.disabled = true;
    }
  });

  function fmtData(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function renderJobs(jobs) {
    document.getElementById("jobs-count").textContent = jobs.length ? jobs.length + " consulta(s)" : "";
    const tbody = document.getElementById("jobs-body");
    if (!jobs.length) {
      tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state">Nenhuma consulta enviada ainda.</div></td></tr>';
      return;
    }
    tbody.innerHTML = jobs.map(j => {
      const progresso = j.total_itens ? `${j.processados} / ${j.total_itens}` : "—";
      let acao = "—";
      if (j.status === "concluido") {
        acao = `<a class="btn" href="/api/consultas/${j.id}/arquivo">Baixar planilha</a>`;
      } else if (j.status === "erro") {
        acao = `<span title="${esc(j.erro_mensagem || "")}" class="flag-no">ver erro</span>`;
      }
      return "<tr>" +
        "<td>" + fmtData(j.criado_em) + "</td>" +
        "<td>" + esc(j.nome_arquivo) + "</td>" +
        "<td>" + esc(j.coluna_nf) + "</td>" +
        "<td>" + esc(STATUS_LABEL[j.status] || j.status) + "</td>" +
        "<td>" + progresso + "</td>" +
        "<td>" + acao + "</td>" +
        "</tr>";
    }).join("");
  }

  async function loadJobs() {
    const resp = await fetch("/api/consultas", { credentials: "same-origin" });
    if (resp.status === 401) { window.location.href = "/login"; return []; }
    const body = await resp.json();
    renderJobs(body.jobs || []);
    return body.jobs || [];
  }

  let pollTimer = null;
  function garantirPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      const jobs = await loadJobs();
      const emAndamento = jobs.some(j => j.status === "pendente" || j.status === "processando");
      if (!emAndamento) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }, 6000);
  }

  loadJobs().then(jobs => {
    const emAndamento = jobs.some(j => j.status === "pendente" || j.status === "processando");
    if (emAndamento) garantirPolling();
  });
})();
