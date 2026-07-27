const state = { routes: [], query: "" };

const elements = {
  body: document.querySelector("#routes-body"),
  empty: document.querySelector("#empty-state"),
  dialog: document.querySelector("#route-dialog"),
  form: document.querySelector("#route-form"),
  error: document.querySelector("#form-error"),
  toast: document.querySelector("#toast"),
};

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function filteredRoutes() {
  const query = state.query.toLowerCase();
  if (!query) return state.routes;
  return state.routes.filter((route) =>
    [route.alias, route.upstream_model, route.base_url].some((value) =>
      value.toLowerCase().includes(query)
    )
  );
}

function render() {
  const routes = filteredRoutes();
  elements.body.innerHTML = routes.map((route) => `
    <tr>
      <td><code class="model-name" title="${escapeHtml(route.alias)}">${escapeHtml(route.alias)}</code></td>
      <td><code title="${escapeHtml(route.upstream_model)}">${escapeHtml(route.upstream_model)}</code></td>
      <td><code class="url" title="${escapeHtml(route.base_url)}">${escapeHtml(route.base_url)}</code></td>
      <td><code>${escapeHtml(route.api_key_masked)}</code></td>
      <td><span class="status ${route.enabled ? "" : "disabled"}">${route.enabled ? "已启用" : "已停用"}</span></td>
      <td class="actions">
        <button class="text-button" type="button" data-edit="${route.id}">编辑</button>
        <button class="danger-button" type="button" data-delete="${route.id}">删除</button>
      </td>
    </tr>
  `).join("");

  elements.empty.hidden = state.routes.length > 0 || state.query.length > 0;
  elements.body.closest("table").hidden = state.routes.length === 0 && !state.query;
  document.querySelector("#total-count").textContent = state.routes.length;
  document.querySelector("#enabled-count").textContent = state.routes.filter((route) => route.enabled).length;
}

async function loadRoutes() {
  const response = await fetch("/admin/api/routes");
  if (!response.ok) throw new Error("无法读取模型路由");
  state.routes = await response.json();
  render();
}

function openDialog(route = null) {
  elements.form.reset();
  elements.error.hidden = true;
  document.querySelector("#route-id").value = route?.id ?? "";
  document.querySelector("#dialog-title").textContent = route ? "编辑路由" : "新增路由";
  document.querySelector("#alias").value = route?.alias ?? "";
  document.querySelector("#upstream-model").value = route?.upstream_model ?? "";
  document.querySelector("#base-url").value = route?.base_url ?? "";
  document.querySelector("#api-key").required = !route;
  document.querySelector("#enabled").checked = route?.enabled ?? true;
  document.querySelector("#key-help").textContent = route
    ? `留空以保留当前 Key (${route.api_key_masked})`
    : "Key 保存在本机 SQLite 数据库中";
  elements.dialog.showModal();
  document.querySelector("#alias").focus();
}

function closeDialog() {
  elements.dialog.close();
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2400);
}

async function saveRoute(event) {
  event.preventDefault();
  const id = document.querySelector("#route-id").value;
  const payload = {
    alias: document.querySelector("#alias").value,
    upstream_model: document.querySelector("#upstream-model").value,
    base_url: document.querySelector("#base-url").value,
    api_key: document.querySelector("#api-key").value || null,
    enabled: document.querySelector("#enabled").checked,
  };
  const button = document.querySelector("#save-route");
  button.disabled = true;
  elements.error.hidden = true;

  try {
    const response = await fetch(id ? `/admin/api/routes/${id}` : "/admin/api/routes", {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const result = await response.json();
      const detail = Array.isArray(result.detail)
        ? result.detail.map((item) => item.msg).join("; ")
        : result.detail;
      throw new Error(detail || "保存失败");
    }
    closeDialog();
    await loadRoutes();
    showToast(id ? "路由已更新" : "路由已创建");
  } catch (error) {
    elements.error.textContent = error.message;
    elements.error.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function deleteRoute(id) {
  const route = state.routes.find((item) => item.id === id);
  if (!route || !window.confirm(`确定删除路由“${route.alias}”吗？`)) return;
  const response = await fetch(`/admin/api/routes/${id}`, { method: "DELETE" });
  if (!response.ok) {
    showToast("删除失败");
    return;
  }
  await loadRoutes();
  showToast("路由已删除");
}

document.querySelector("#add-route").addEventListener("click", () => openDialog());
document.querySelector("#empty-add").addEventListener("click", () => openDialog());
document.querySelector("#close-dialog").addEventListener("click", closeDialog);
document.querySelector("#cancel-dialog").addEventListener("click", closeDialog);
elements.form.addEventListener("submit", saveRoute);
elements.dialog.addEventListener("click", (event) => {
  if (event.target === elements.dialog) closeDialog();
});
document.querySelector("#search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  render();
});
elements.body.addEventListener("click", (event) => {
  const editId = Number(event.target.dataset.edit);
  const deleteId = Number(event.target.dataset.delete);
  if (editId) openDialog(state.routes.find((route) => route.id === editId));
  if (deleteId) deleteRoute(deleteId);
});

document.querySelector("#gateway-endpoint").textContent = `${window.location.origin}/v1`;
fetch("/health")
  .then((response) => {
    if (!response.ok) throw new Error();
    const health = document.querySelector("#health");
    health.className = "health online";
    health.innerHTML = "<span></span>网关运行中";
  })
  .catch(() => {
    const health = document.querySelector("#health");
    health.className = "health offline";
    health.innerHTML = "<span></span>网关不可用";
  });

loadRoutes().catch((error) => showToast(error.message));
