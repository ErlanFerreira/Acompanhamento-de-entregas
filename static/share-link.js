(function () {
  "use strict";

  const btn = document.getElementById("btn-link-acesso");
  if (!btn) return;

  const original = btn.textContent;

  btn.addEventListener("click", async function () {
    try {
      const resp = await fetch("/api/link-acesso", { credentials: "same-origin" });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      const body = await resp.json();
      await navigator.clipboard.writeText(body.url);
      btn.textContent = "Link copiado!";
    } catch (e) {
      btn.textContent = "Erro ao copiar";
    } finally {
      setTimeout(() => { btn.textContent = original; }, 2500);
    }
  });
})();
