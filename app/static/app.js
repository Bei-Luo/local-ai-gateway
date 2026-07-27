const state = { routes: [], usage: [], query: "", gatewayToken: null, tokenStatus: null };

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
    [route.site_name, route.alias, route.upstream_model, route.note, route.base_url].some((value) =>
      value.toLowerCase().includes(query)
    )
  );
}

function render() {
  const routes = filteredRoutes();
  elements.body.innerHTML = routes.map((route) => `
    <tr>
      <td><span class="site-name" title="${escapeHtml(route.site_name)}">${escapeHtml(route.site_name || "-")}</span></td>
      <td><code class="model-name" title="${escapeHtml(route.alias)}">${escapeHtml(route.alias)}</code></td>
      <td><code title="${escapeHtml(route.upstream_model)}">${escapeHtml(route.upstream_model)}</code></td>
      <td><span class="note" title="${escapeHtml(route.note)}">${escapeHtml(route.note || "-")}</span></td>
      <td><code class="url" title="${escapeHtml(route.base_url)}">${escapeHtml(route.base_url)}</code></td>
      <td><code>${escapeHtml(route.api_key_masked)}</code></td>
      <td><span class="status ${route.enabled ? "" : "disabled"}">${route.enabled ? "已启用" : "已停用"}</span></td>
      <td class="actions">
        <button class="text-button" type="button" data-edit="${route.id}">编辑</button>
        <button class="route-switch" type="button" role="switch" aria-checked="${route.enabled}" title="${route.enabled ? "停用路由" : "启用此线路并停用其他同名线路"}" data-toggle="${route.id}">
          <span aria-hidden="true"></span><span class="sr-only">${route.enabled ? "停用路由" : "启用路由"}</span>
        </button>
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

function formatTimestamp(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function renderUsage() {
  const body = document.querySelector("#usage-body");
  const empty = document.querySelector("#usage-empty");
  body.innerHTML = state.usage.map((record) => `
    <tr>
      <td><code>${escapeHtml(formatTimestamp(record.created_at))}</code></td>
      <td><span class="usage-site">${escapeHtml(record.site_name || "-")}</span></td>
      <td><code>${escapeHtml(record.model_alias || "-")}</code></td>
      <td><span class="request-type">${escapeHtml(record.request_type || "-")}${record.streamed ? '<small>流式</small>' : ""}</span></td>
      <td><span class="request-status ${record.status_code < 400 ? "success" : "error"}">${record.status_code}</span></td>
      <td><code>${record.ttft_ms == null ? "-" : `${record.ttft_ms} ms`}</code></td>
      <td><code>${record.duration_ms} ms</code></td>
    </tr>
  `).join("");
  body.closest("table").hidden = state.usage.length === 0;
  empty.hidden = state.usage.length > 0;
}

async function loadUsage() {
  const response = await fetch("/admin/api/usage?limit=100");
  if (!response.ok) throw new Error("无法读取使用记录");
  state.usage = await response.json();
  renderUsage();
}

function renderToken() {
  const status = state.tokenStatus;
  const input = document.querySelector("#gateway-token");
  const help = document.querySelector("#token-help");
  const generate = document.querySelector("#generate-token");
  const copy = document.querySelector("#copy-token");
  const disable = document.querySelector("#disable-token");
  input.value = state.gatewayToken || status?.masked || "未生成";
  copy.hidden = !state.gatewayToken;
  disable.hidden = !status?.configured || status?.source === "environment";
  generate.hidden = status?.source === "environment";
  generate.textContent = status?.configured ? "重新生成" : "生成令牌";
  help.textContent = status?.source === "environment"
    ? "令牌由 GATEWAY_API_KEY 环境变量管理"
    : status?.configured
      ? "令牌已启用；重新生成后旧令牌立即失效"
      : "为 OpenCode 生成本地网关访问令牌";
}

async function loadTokenStatus() {
  const response = await fetch("/admin/api/gateway-token");
  if (!response.ok) throw new Error("无法读取令牌状态");
  state.tokenStatus = await response.json();
  renderToken();
}

async function generateToken() {
  if (state.tokenStatus?.configured && !window.confirm("重新生成后，当前令牌会立即失效。继续吗？")) return;
  const response = await fetch("/admin/api/gateway-token", { method: "POST" });
  const result = await response.json();
  if (!response.ok) {
    showToast(result.detail || "令牌生成失败");
    return;
  }
  state.gatewayToken = result.token;
  state.tokenStatus = { configured: true, masked: result.masked, source: result.source };
  renderToken();
  showToast("令牌已生成，请复制到 OpenCode 配置");
}

async function copyToken() {
  if (!state.gatewayToken) return;
  try {
    await navigator.clipboard.writeText(state.gatewayToken);
    showToast("令牌已复制");
  } catch (_) {
    const input = document.querySelector("#gateway-token");
    input.select();
    document.execCommand("copy");
    showToast("令牌已复制");
  }
}

async function disableToken() {
  if (!window.confirm("停用后，网关的 /v1 接口将不再要求本地令牌。继续吗？")) return;
  const response = await fetch("/admin/api/gateway-token", { method: "DELETE" });
  if (!response.ok) {
    showToast("令牌停用失败");
    return;
  }
  state.gatewayToken = null;
  state.tokenStatus = { configured: false, masked: null, source: "none" };
  renderToken();
  showToast("网关令牌已停用");
}

async function clearUsage() {
  if (!state.usage.length || !window.confirm("确定清空全部使用记录吗？")) return;
  const response = await fetch("/admin/api/usage", { method: "DELETE" });
  if (!response.ok) {
    showToast("使用记录清空失败");
    return;
  }
  state.usage = [];
  renderUsage();
  showToast("使用记录已清空");
}

function openDialog(route = null) {
  elements.form.reset();
  elements.error.hidden = true;
  document.querySelector("#route-id").value = route?.id ?? "";
  document.querySelector("#dialog-title").textContent = route ? "编辑路由" : "新增路由";
  document.querySelector("#site-name").value = route?.site_name ?? "";
  document.querySelector("#alias").value = route?.alias ?? "";
  document.querySelector("#upstream-model").value = route?.upstream_model ?? "";
  document.querySelector("#route-note").value = route?.note ?? "";
  document.querySelector("#base-url").value = route?.base_url ?? "";
  document.querySelector("#api-key").required = !route;
  document.querySelector("#enabled").checked = route?.enabled ?? true;
  document.querySelector("#key-help").textContent = route
    ? `留空以保留当前 Key (${route.api_key_masked})`
    : "Key 保存在本机 SQLite 数据库中";
  const detectionStatus = document.querySelector("#detection-status");
  detectionStatus.textContent = "填写上游地址和 Key 后可检测模型";
  detectionStatus.classList.remove("error");
  document.querySelector("#detected-model-list").replaceChildren();
  elements.dialog.showModal();
  document.querySelector("#alias").focus();
}

async function detectModels() {
  const baseUrlInput = document.querySelector("#base-url");
  const apiKeyInput = document.querySelector("#api-key");
  const routeId = document.querySelector("#route-id").value;
  const button = document.querySelector("#detect-models");
  const status = document.querySelector("#detection-status");
  const modelList = document.querySelector("#detected-model-list");

  if (!baseUrlInput.reportValidity()) return;
  if (!apiKeyInput.value && !routeId) {
    apiKeyInput.setCustomValidity("请输入 API Key 后再检测");
    apiKeyInput.reportValidity();
    apiKeyInput.setCustomValidity("");
    return;
  }

  button.disabled = true;
  button.textContent = "检测中...";
  status.textContent = "正在连接上游模型接口";
  status.classList.remove("error");
  modelList.replaceChildren();

  try {
    const response = await fetch("/admin/api/discover-models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_url: baseUrlInput.value,
        api_key: apiKeyInput.value || null,
        route_id: routeId ? Number(routeId) : null,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "模型检测失败");

    if (result.models.length === 0) {
      status.textContent = "连接成功，但上游未返回模型";
      return;
    }
    for (const model of result.models) {
      const option = document.createElement("option");
      option.value = model;
      modelList.append(option);
    }
    baseUrlInput.value = result.base_url;
    status.textContent = `检测到 ${result.models.length} 个模型，可在上游模型名中搜索选择`;
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    button.disabled = false;
    button.textContent = "检测可用模型";
  }
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
    site_name: document.querySelector("#site-name").value,
    alias: document.querySelector("#alias").value,
    upstream_model: document.querySelector("#upstream-model").value,
    note: document.querySelector("#route-note").value,
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

async function toggleRoute(id) {
  const route = state.routes.find((item) => item.id === id);
  if (!route) return;
  const button = elements.body.querySelector(`[data-toggle="${id}"]`);
  if (button) button.disabled = true;
  const response = await fetch(`/admin/api/routes/${id}/enabled`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: !route.enabled }),
  });
  if (!response.ok) {
    if (button) button.disabled = false;
    showToast(route.enabled ? "停用失败" : "启用失败");
    return;
  }
  await loadRoutes();
  showToast(route.enabled ? "路由已停用" : "路由已启用");
}

document.querySelector("#add-route").addEventListener("click", () => openDialog());
document.querySelector("#empty-add").addEventListener("click", () => openDialog());
document.querySelector("#close-dialog").addEventListener("click", closeDialog);
document.querySelector("#cancel-dialog").addEventListener("click", closeDialog);
document.querySelector("#generate-token").addEventListener("click", generateToken);
document.querySelector("#copy-token").addEventListener("click", copyToken);
document.querySelector("#disable-token").addEventListener("click", disableToken);
document.querySelector("#refresh-usage").addEventListener("click", () => loadUsage().catch((error) => showToast(error.message)));
document.querySelector("#clear-usage").addEventListener("click", clearUsage);
document.querySelector("#detect-models").addEventListener("click", detectModels);
elements.form.addEventListener("submit", saveRoute);
elements.dialog.addEventListener("click", (event) => {
  if (event.target === elements.dialog) closeDialog();
});
document.querySelector("#search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  render();
});
elements.body.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !elements.body.contains(button)) return;
  const editId = Number(button.dataset.edit);
  const toggleId = Number(button.dataset.toggle);
  const deleteId = Number(button.dataset.delete);
  if (editId) openDialog(state.routes.find((route) => route.id === editId));
  if (toggleId) toggleRoute(toggleId);
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

Promise.all([loadRoutes(), loadUsage(), loadTokenStatus()]).catch((error) => showToast(error.message));
window.setInterval(() => loadUsage().catch(() => {}), 10000);
