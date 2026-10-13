(function () {
  "use strict";

  // Cadastro dos analistas (só o supervisor chega aqui). Cada analista tem
  // nome, login, senha e a lista de empresas que pode ver -- nomes exatamente
  // como aparecem nos CT-e (tomador ou remetente), vindos de /api/empresas.

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function fmtN(n) { return Number(n || 0).toLocaleString("pt-BR"); }

  let usuarios = [];
  let empresas = null;  // carregadas na primeira abertura do formulário
  let editando = null;  // analista em edição (null = novo)
  let marcadas = new Set();

  const modal = document.getElementById("usr-modal");
  const $ = id => document.getElementById(id);

  async function api(url, opts) {
    const resp = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, opts || {}));
    if (resp.status === 401) { window.location.href = "/login"; throw new Error("Sessão expirada."); }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.erro || "Falha na operação.");
    return body;
  }

  async function carregarUsuarios() {
    usuarios = (await api("/api/usuarios")).usuarios;
    renderUsuarios();
  }

  function renderUsuarios() {
    const ativos = usuarios.filter(u => u.ativo).length;
    $("usuarios-count").textContent = fmtN(usuarios.length) + " cadastrado(s) · " + fmtN(ativos) + " ativo(s)";
    const tbody = $("usuarios-body");
    if (!usuarios.length) {
      tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state">Nenhum analista cadastrado ainda. Clique em "+ Novo analista".</div></td></tr>';
      return;
    }
    tbody.innerHTML = usuarios.map((u, i) => {
      const emp = u.empresas.length
        ? esc(u.empresas.slice(0, 3).join(", ")) + (u.empresas.length > 3 ? ' <span class="bip-muted">+' + (u.empresas.length - 3) + "</span>" : "")
        : '<span class="flag-no">nenhuma (não vê CT-e)</span>';
      return '<tr' + (u.ativo ? "" : ' class="usr-inativo"') + '>' +
        '<td><strong>' + esc(u.nome) + '</strong></td>' +
        '<td>' + esc(u.login) + '</td>' +
        '<td title="' + esc(u.empresas.join("\n")) + '">' + emp + '</td>' +
        '<td>' + (u.ativo ? '<span class="bip-tag faturado">Ativo</span>' : '<span class="bip-tag">Desativado</span>') +
          (u.ativo && u.trocar_senha ? ' <span class="bip-tag" title="Ainda não trocou a senha definida por você">senha provisória</span>' : "") + '</td>' +
        '<td class="bip-acoes"><button type="button" class="btn" data-editar="' + i + '">Editar</button> ' +
        '<button type="button" class="btn" data-ativar="' + i + '">' + (u.ativo ? "Desativar" : "Reativar") + '</button></td>' +
        '</tr>';
    }).join("");
    tbody.querySelectorAll("[data-editar]").forEach(b => b.addEventListener("click", () => abrir(usuarios[+b.dataset.editar])));
    tbody.querySelectorAll("[data-ativar]").forEach(b => b.addEventListener("click", () => alternarAtivo(usuarios[+b.dataset.ativar])));
  }

  async function alternarAtivo(u) {
    const acao = u.ativo ? "Desativar" : "Reativar";
    if (!confirm(acao + " o acesso de " + u.nome + "?" + (u.ativo ? " O acesso é cortado na hora." : ""))) return;
    try {
      await api("/api/usuarios/" + u.id, { method: "PUT", body: JSON.stringify({ nome: u.nome, login: u.login, empresas: u.empresas, ativo: !u.ativo }) });
      await carregarUsuarios();
    } catch (e) { alert(e.message); }
  }

  // ---- formulário ----
  async function abrir(u) {
    editando = u || null;
    marcadas = new Set(u ? u.empresas : []);
    $("usr-titulo").textContent = u ? "Editar analista" : "Novo analista";
    $("usr-nome").value = u ? u.nome : "";
    $("usr-login").value = u ? u.login : "";
    $("usr-senha").value = "";
    $("usr-senha").required = !u;
    $("usr-senha-hint").textContent = u
      ? "(deixe em branco para manter; se preencher, o analista troca no próximo acesso)"
      : "(provisória: o analista troca no primeiro acesso)";
    $("usr-ativo").checked = u ? u.ativo : true;
    $("usr-ativo-wrap").hidden = !u;
    $("usr-busca").value = "";
    $("usr-so-marcadas").checked = !!(u && u.empresas.length);
    $("usr-erro").textContent = "";
    modal.hidden = false;
    $("usr-nome").focus();
    if (!empresas) {
      try { empresas = (await api("/api/empresas")).empresas; } catch (e) { $("usr-erro").textContent = e.message; empresas = []; }
    }
    renderEmpresas();
  }
  function fechar() { modal.hidden = true; editando = null; }

  function renderEmpresas() {
    const q = $("usr-busca").value.trim().toUpperCase();
    const soMarcadas = $("usr-so-marcadas").checked;
    // Empresas marcadas que não aparecem mais nos CT-e sincronizados continuam na lista.
    const todas = (empresas || []).slice();
    marcadas.forEach(n => { if (!todas.some(e => e.nome === n)) todas.push({ nome: n, tomador: 0, remetente: 0 }); });
    const lista = todas.filter(e => (!soMarcadas || marcadas.has(e.nome)) && (!q || e.nome.includes(q)));
    const MAX = 300;
    $("usr-empresas").innerHTML = lista.length
      ? lista.slice(0, MAX).map(e => {
          const papeis = [e.tomador ? fmtN(e.tomador) + " como tomador" : "", e.remetente ? fmtN(e.remetente) + " como remetente" : ""].filter(Boolean).join(" · ") || "sem CT-e na janela atual";
          return '<label class="usr-empresa"><input type="checkbox" value="' + esc(e.nome) + '"' + (marcadas.has(e.nome) ? " checked" : "") + '>' +
            '<span class="usr-empresa-nome">' + esc(e.nome) + '</span><span class="comp-de">' + esc(papeis) + '</span></label>';
        }).join("") + (lista.length > MAX ? '<div class="comp-de">Mostrando ' + MAX + ' de ' + fmtN(lista.length) + ' -- refine a busca.</div>' : "")
      : '<div class="comp-de">' + (soMarcadas ? "Nenhuma empresa marcada ainda -- desmarque \"só as marcadas\" para escolher." : "Nenhuma empresa encontrada.") + '</div>';
    $("usr-empresas").querySelectorAll("input[type=checkbox]").forEach(cb => cb.addEventListener("change", () => {
      if (cb.checked) marcadas.add(cb.value); else marcadas.delete(cb.value);
      renderSelecionadas();
    }));
    renderSelecionadas();
  }

  function renderSelecionadas() {
    $("usr-sel-count").textContent = "· " + fmtN(marcadas.size) + " marcada(s)";
    $("usr-selecionadas").innerHTML = Array.from(marcadas).sort().map(n =>
      '<span class="usr-chip">' + esc(n) + '<button type="button" data-tirar="' + esc(n) + '" aria-label="Tirar">✕</button></span>'
    ).join("");
    $("usr-selecionadas").querySelectorAll("[data-tirar]").forEach(b => b.addEventListener("click", () => {
      marcadas.delete(b.dataset.tirar);
      renderEmpresas();
    }));
  }

  async function salvar(e) {
    e.preventDefault();
    const dados = {
      nome: $("usr-nome").value, login: $("usr-login").value, senha: $("usr-senha").value,
      empresas: Array.from(marcadas)
    };
    if (editando) dados.ativo = $("usr-ativo").checked;
    if (!dados.empresas.length && !confirm("Nenhuma empresa marcada: esse analista não vai ver nenhum CT-e. Salvar mesmo assim?")) return;
    $("usr-salvar").disabled = true;
    $("usr-erro").textContent = "";
    try {
      await api(editando ? "/api/usuarios/" + editando.id : "/api/usuarios", { method: editando ? "PUT" : "POST", body: JSON.stringify(dados) });
      fechar();
      await carregarUsuarios();
    } catch (err) {
      $("usr-erro").textContent = err.message;
    } finally {
      $("usr-salvar").disabled = false;
    }
  }

  let buscaT;
  $("usr-busca").addEventListener("input", () => { clearTimeout(buscaT); buscaT = setTimeout(renderEmpresas, 120); });
  $("usr-so-marcadas").addEventListener("change", renderEmpresas);
  $("usr-form").addEventListener("submit", salvar);
  $("usr-cancelar").addEventListener("click", fechar);
  $("usr-fechar").addEventListener("click", fechar);
  $("btn-novo").addEventListener("click", () => abrir(null));
  modal.addEventListener("click", e => { if (e.target === modal) fechar(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !modal.hidden) fechar(); });

  carregarUsuarios().catch(e => {
    $("usuarios-body").innerHTML = '<tr><td colspan="5"><div class="empty-state">' + esc(e.message) + '</div></td></tr>';
  });
})();
