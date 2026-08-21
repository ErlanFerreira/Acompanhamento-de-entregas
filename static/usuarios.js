(function () {
  "use strict";

  const modal = document.getElementById("user-modal");
  const modalTitle = document.getElementById("modal-title");
  const modalError = document.getElementById("modal-error");
  const idEl = document.getElementById("u-id");
  const nomeEl = document.getElementById("u-nome");
  const emailEl = document.getElementById("u-email");
  const senhaEl = document.getElementById("u-senha");
  const senhaHint = document.getElementById("senha-hint");
  const roleEl = document.getElementById("u-role");

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

  async function loadUsers() {
    const resp = await fetch("/api/usuarios", { credentials: "same-origin" });
    if (resp.status === 401) { window.location.href = "/login"; return; }
    const body = await resp.json();
    renderUsers(body.usuarios || []);
  }

  function renderUsers(users) {
    document.getElementById("users-count").textContent = users.length + (users.length === 1 ? " usuário" : " usuários");
    const tbody = document.getElementById("users-body");
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state">Nenhum usuário cadastrado.</div></td></tr>';
      return;
    }
    tbody.innerHTML = users.map(u => (
      "<tr>" +
      "<td>" + esc(u.nome || "—") + "</td>" +
      "<td>" + esc(u.email) + "</td>" +
      '<td><span class="role-pill ' + (u.role === "admin" ? "admin" : "") + '">' + (u.role === "admin" ? "Admin" : "Usuário") + "</span></td>" +
      "<td>" + esc(u.created_at || "—") + "</td>" +
      '<td style="text-align:right; white-space:nowrap;">' +
      '<button class="btn" data-action="edit" data-id="' + u.id + '">Editar</button> ' +
      '<button class="btn" data-action="delete" data-id="' + u.id + '">Remover</button>' +
      "</td>" +
      "</tr>"
    )).join("");

    tbody.querySelectorAll('button[data-action="edit"]').forEach(btn => {
      btn.addEventListener("click", () => openEdit(users.find(u => String(u.id) === btn.dataset.id)));
    });
    tbody.querySelectorAll('button[data-action="delete"]').forEach(btn => {
      btn.addEventListener("click", () => removeUser(btn.dataset.id));
    });
  }

  function openNew() {
    modalTitle.textContent = "Novo usuário";
    idEl.value = "";
    nomeEl.value = "";
    emailEl.value = "";
    emailEl.disabled = false;
    senhaEl.value = "";
    senhaHint.textContent = "(obrigatória)";
    roleEl.value = "user";
    modalError.classList.remove("show");
    modal.style.display = "flex";
  }

  function openEdit(user) {
    if (!user) return;
    modalTitle.textContent = "Editar usuário";
    idEl.value = user.id;
    nomeEl.value = user.nome || "";
    emailEl.value = user.email;
    emailEl.disabled = true;
    senhaEl.value = "";
    senhaHint.textContent = "(deixe em branco para manter)";
    roleEl.value = user.role;
    modalError.classList.remove("show");
    modal.style.display = "flex";
  }

  function closeModal() { modal.style.display = "none"; }

  async function saveUser() {
    const id = idEl.value;
    modalError.classList.remove("show");
    try {
      let resp;
      if (id) {
        resp = await fetch("/api/usuarios/" + id, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nome: nomeEl.value, role: roleEl.value, senha: senhaEl.value || undefined })
        });
      } else {
        resp = await fetch("/api/usuarios", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nome: nomeEl.value, email: emailEl.value, senha: senhaEl.value, role: roleEl.value })
        });
      }
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.erro || "Falha ao salvar.");
      closeModal();
      loadUsers();
    } catch (e) {
      modalError.textContent = e.message;
      modalError.classList.add("show");
    }
  }

  async function removeUser(id) {
    if (!confirm("Remover este usuário? Essa ação não pode ser desfeita.")) return;
    const resp = await fetch("/api/usuarios/" + id, { method: "DELETE", credentials: "same-origin" });
    const body = await resp.json();
    if (!resp.ok) { alert(body.erro || "Falha ao remover."); return; }
    loadUsers();
  }

  document.getElementById("btn-novo").addEventListener("click", openNew);
  document.getElementById("modal-cancel").addEventListener("click", closeModal);
  document.getElementById("modal-save").addEventListener("click", saveUser);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });

  loadUsers();
})();
