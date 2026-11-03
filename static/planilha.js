// Baixa o relatório da tela como planilha .xlsx no padrão Grupo Transrota
// (montada no servidor: app/planilha_marca.py). Se o servidor não responder,
// cai no CSV de antes (`fallback`) pra nunca ficar sem exportar.
(function () {
  "use strict";

  async function compactar(texto) {
    if (typeof CompressionStream === "undefined") return null;
    try {
      const fluxo = new Blob([texto]).stream().pipeThrough(new CompressionStream("gzip"));
      return await new Response(fluxo).blob();
    } catch (e) { return null; }
  }

  // rel: { nome, titulo, subtitulo, info: [[rotulo, valor]], colunas: [{t, w, tipo, total, cores}], linhas: [[...]] }
  window.baixarPlanilha = async function (rel, fallback) {
    try {
      const texto = JSON.stringify(rel);
      const gz = await compactar(texto);
      const resp = await fetch("/api/exportar-xlsx", {
        method: "POST",
        credentials: "same-origin",
        headers: gz ? { "Content-Type": "application/octet-stream", "X-Conteudo-Gzip": "1" } : { "Content-Type": "application/json" },
        body: gz || texto
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = rel.nome.replace(/\.xlsx$/i, "") + ".xlsx";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(a.href);
    } catch (e) {
      console.warn("Planilha .xlsx indisponível, baixando CSV:", e);
      if (fallback) fallback();
    }
  };
})();
