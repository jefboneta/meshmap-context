const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
};

const graph = { nodes: [], edges: [], groups: [], comments: [] };
let config = {};
let selectedId = null;
let editingId = null;
let parentIdOnCreate = null;
let linking = false;
let linkSource = null;
let semanticEnabled = false;
let viewedMap = null;
let networkSearchTimer;
let toastTimer;
const byId = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function notify(message) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2600);
}

async function saveGraph() {
  if (viewedMap) return;
  try {
    const result = await api("/api/graph", { method: "PUT", body: JSON.stringify(graph) });
    if (result.network_error) notify(`Saved locally. Network publish issue: ${result.network_error}`);
  } catch (error) {
    notify(`Could not save map: ${error.message}`);
  }
}

function activeGraph() { return viewedMap?.graph || graph; }
function nodeById(id) { return activeGraph().nodes.find((node) => node.id === id); }
function centerPosition() {
  const board = byId("board");
  return { x: Math.max(30, board.scrollLeft + board.clientWidth / 2 - 109), y: Math.max(30, board.scrollTop + board.clientHeight / 2 - 55) };
}

function render() {
  const current = activeGraph();
  const query = byId("search").value.trim().toLowerCase();
  const list = byId("node-list");
  const layer = byId("nodes-layer");
  const svg = byId("edges-layer");
  list.replaceChildren();
  layer.replaceChildren();
  svg.replaceChildren();

  const visibleNodes = current.nodes.filter((node) => !query || `${node.name} ${node.description}`.toLowerCase().includes(query));
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const groups = current.groups || [];
  [...groups, { id: "", name: "Ungrouped" }].forEach((group) => {
    const groupNodes = visibleNodes.filter((node) => (node.groupId || "") === group.id);
    if (!groupNodes.length && !group.id) return;
    const section = el("section", "node-group");
    const heading = el("div", "node-group-heading");
    heading.append(el("strong", "", group.name), el("small", "", String(groupNodes.length)));
    section.append(heading);
    const nodesById = new Map(groupNodes.map((node) => [node.id, node]));
    const childrenById = new Map();
    groupNodes.forEach((node) => {
      if (!nodesById.has(node.parentId)) return;
      if (!childrenById.has(node.parentId)) childrenById.set(node.parentId, []);
      childrenById.get(node.parentId).push(node);
    });
    const rendered = new Set();
    const appendNode = (node, depth = 0) => {
      if (rendered.has(node.id)) return;
      rendered.add(node.id);
      const item = el("div", `node-list-item${selectedId === node.id ? " selected" : ""}`);
      item.style.paddingLeft = `${10 + Math.min(depth, 4) * 13}px`;
      item.append(el("strong", "", node.name), el("small", "", node.description || "No description yet"));
      item.addEventListener("click", () => { selectedId = node.id; render(); });
      section.append(item);
      (childrenById.get(node.id) || []).forEach((child) => appendNode(child, depth + 1));
    };
    groupNodes.filter((node) => !visibleIds.has(node.parentId) || !nodesById.has(node.parentId)).forEach((node) => appendNode(node));
    groupNodes.forEach((node) => appendNode(node));
    list.append(section);
  });

  const ns = "http://www.w3.org/2000/svg";
  current.edges.forEach((edge) => {
    const source = nodeById(edge.source);
    const target = nodeById(edge.target);
    if (!source || !target) return;
    const sx = source.x + 109;
    const sy = source.y + 42;
    const tx = target.x + 109;
    const ty = target.y + 42;
    const bend = Math.max(48, Math.abs(tx - sx) * 0.35);
    const curve = document.createElementNS(ns, "path");
    curve.setAttribute("d", `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`);
    curve.setAttribute("class", "edge-line");
    svg.append(curve);
    if (edge.label) {
      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", String((sx + tx) / 2));
      label.setAttribute("y", String((sy + ty) / 2 - 8));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "edge-label");
      label.textContent = edge.label;
      svg.append(label);
    }
  });

  current.nodes.forEach((node) => {
    const card = el("article", `map-node${selectedId === node.id ? " selected" : ""}${linkSource === node.id ? " link-source" : ""}`);
    card.style.left = `${node.x}px`;
    card.style.top = `${node.y}px`;
    const title = el("strong", "", node.name);
    const desc = el("p", "", node.description || "Add a description to explain this node.");
    card.append(title, desc);
    card.addEventListener("click", () => chooseNode(node.id));
    card.addEventListener("pointerdown", (event) => beginDrag(event, node, card));
    layer.append(card);
  });

  byId("empty-state").hidden = current.nodes.length > 0;
  byId("map-count").textContent = `${current.nodes.length} nodes · ${current.edges.length} links`;
  byId("workspace-title-text").textContent = viewedMap?.title || config.workspace_title || "Farmers' Market";
  byId("return-local").hidden = !viewedMap;
  byId("link-mode").hidden = Boolean(viewedMap);
  byId("add-node").disabled = Boolean(viewedMap);
  byId("add-group").disabled = Boolean(viewedMap);
  byId("empty-add").disabled = Boolean(viewedMap);
  byId("publish-state").textContent = viewedMap ? "Public · Read only" : (config.publish_enabled ? "Publicly discoverable" : "Private map");
  byId("board-hint").textContent = linking ? (linkSource ? "Choose the node this relationship points to" : "Choose the starting node") : "Select a node to inspect it";
  renderInspector();
}

function beginDrag(event, node, card) {
  if (event.button !== 0 || linking || viewedMap) return;
  event.preventDefault();
  const startX = event.clientX;
  const startY = event.clientY;
  const originX = node.x;
  const originY = node.y;
  card.setPointerCapture(event.pointerId);
  const move = (moveEvent) => {
    node.x = Math.max(0, originX + moveEvent.clientX - startX);
    node.y = Math.max(0, originY + moveEvent.clientY - startY);
    card.style.left = `${node.x}px`;
    card.style.top = `${node.y}px`;
    renderEdges();
  };
  const end = () => {
    card.removeEventListener("pointermove", move);
    card.removeEventListener("pointerup", end);
    card.removeEventListener("pointercancel", end);
    saveGraph();
  };
  card.addEventListener("pointermove", move);
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

function renderEdges() {
  const current = activeGraph();
  const svg = byId("edges-layer");
  svg.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  current.edges.forEach((edge) => {
    const source = nodeById(edge.source);
    const target = nodeById(edge.target);
    if (!source || !target) return;
    const sx = source.x + 109, sy = source.y + 42;
    const tx = target.x + 109, ty = target.y + 42;
    const bend = Math.max(48, Math.abs(tx - sx) * 0.35);
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`);
    path.setAttribute("class", "edge-line");
    svg.append(path);
    if (edge.label) {
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", String((sx + tx) / 2));
      text.setAttribute("y", String((sy + ty) / 2 - 8));
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("class", "edge-label");
      text.textContent = edge.label;
      svg.append(text);
    }
  });
}

function chooseNode(id) {
  if (viewedMap) {
    selectedId = id;
    render();
    return;
  }
  if (linking) {
    if (!linkSource) {
      linkSource = id;
      render();
      return;
    }
    if (id === linkSource) return;
    const source = nodeById(linkSource);
    const target = nodeById(id);
    const label = window.prompt(`How does “${source.name}” relate to “${target.name}”?`, "leads to");
    if (label !== null) {
      graph.edges.push({ id: crypto.randomUUID(), source: linkSource, target: id, label: label.trim() });
      saveGraph();
    }
    linking = false;
    linkSource = null;
    byId("link-mode").classList.remove("active");
    render();
    return;
  }
  selectedId = id;
  render();
}

function renderInspector() {
  const panel = byId("inspector");
  const node = nodeById(selectedId);
  if (!node) {
    const blank = el("div", "blank-inspector");
    blank.append(el("span", "eyebrow", "NODE DETAILS"), el("div", "blank-mark", "↗"), el("p", "", "Select a node on the map to view its description, relationships, and comments."));
    panel.replaceChildren(blank);
    return;
  }
  const content = document.createDocumentFragment();
  const titleRow = el("div", "detail-title-row");
  const titleGroup = document.createElement("div");
  titleGroup.append(el("span", "eyebrow", "NODE DETAILS"), el("h2", "", node.name));
  const actions = el("div", "detail-actions");
  const addChild = el("button", "small-action", "Add child");
  addChild.type = "button";
  addChild.addEventListener("click", () => openNodeDialog(null, node));
  const edit = el("button", "small-action", "Edit");
  edit.type = "button";
  edit.addEventListener("click", () => openNodeDialog(node));
  const remove = el("button", "small-action", "Delete");
  remove.type = "button";
  remove.addEventListener("click", () => deleteNode(node));
  if (!viewedMap) actions.append(addChild, edit, remove);
  titleRow.append(titleGroup, actions);
  content.append(titleRow, el("p", "detail-description", node.description || "No description has been added."));
  const reviewPath = el("button", "small-action path-review", "Ask how this path could be better");
  reviewPath.type = "button";
  reviewPath.addEventListener("click", () => sendChat(`Review the path to “${node.name}”. Based on the concept, descriptions, relationships, and comments, suggest a clearer or more useful path. Identify specific missing or misordered nodes, and explain why.`));
  content.append(reviewPath);

  const current = activeGraph();
  const relations = current.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
  const relationSection = el("section", "detail-section");
  relationSection.append(el("h3", "", "Relationships"));
  if (!relations.length) relationSection.append(el("div", "relationship-item", "No linked nodes yet."));
  relations.forEach((edge) => {
    const other = nodeById(edge.source === node.id ? edge.target : edge.source);
    if (!other) return;
    const row = el("div", "relationship-item");
    row.append(el("b", "", edge.source === node.id ? "Leads to " : "Linked from "), document.createTextNode(`${other.name}${edge.label ? ` · ${edge.label}` : ""}`));
    relationSection.append(row);
    if (semanticEnabled && edge.source === node.id) {
      const check = el("button", "small-action", "Check relationship");
      check.type = "button";
      check.addEventListener("click", () => checkSemantic(edge));
      relationSection.append(check);
    }
  });
  content.append(relationSection);

  const comments = el("section", "detail-section");
  const visibleComments = viewedMap ? (viewedMap.comments || []) : graph.comments;
  comments.append(el("h3", "", `Comments · ${visibleComments.filter((item) => item.nodeId === node.id).length}`));
  visibleComments.filter((item) => item.nodeId === node.id).forEach((comment) => {
    const item = el("div", "comment-item");
    item.append(el("p", "", comment.text), el("small", "", `${comment.author ? `${comment.author} · ` : ""}${new Date(comment.createdAt).toLocaleString()}`));
    comments.append(item);
  });
  const form = el("form", "comment-form");
  const input = document.createElement("textarea");
  input.rows = 2;
  input.maxLength = 1000;
  input.placeholder = "Add a comment to this node…";
  input.setAttribute("aria-label", "Add a comment");
  const submit = el("button", "small-action", "Comment");
  submit.type = "submit";
  form.append(input, submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!input.value.trim()) return;
    const text = input.value.trim();
    if (viewedMap) {
      api("/api/network/comments", { method: "POST", body: JSON.stringify({ owner_id: viewedMap.owner_id, node_id: node.id, text }) })
        .then((result) => { viewedMap.comments.push(result.comment); render(); })
        .catch((error) => notify(error.message));
    } else {
      graph.comments.push({ id: crypto.randomUUID(), nodeId: node.id, text, createdAt: new Date().toISOString(), author: "Map owner" });
      saveGraph();
      render();
    }
  });
  comments.append(form);
  content.append(comments);
  panel.replaceChildren(content);
}

async function checkSemantic(edge) {
  const source = nodeById(edge.source), target = nodeById(edge.target);
  if (!source || !target) return;
  notify("Checking the relationship…");
  try {
    const result = await api("/api/semantic", { method: "POST", body: JSON.stringify({ source, target, relationship: edge.label }) });
    const output = el("div", "semantic-result");
    output.append(el("strong", "", `${result.confidence_percent}% model confidence`), el("div", "", result.explanation));
    byId("inspector").append(output);
  } catch (error) {
    notify(error.message);
  }
}

function openNodeDialog(node = null, parentNode = null) {
  editingId = node?.id || null;
  parentIdOnCreate = node ? null : (parentNode?.id || null);
  byId("node-dialog-title").textContent = node ? "Edit node" : (parentNode ? "Add a child node" : "Add a node");
  byId("node-name").value = node?.name || "";
  byId("node-description").value = node?.description || "";
  const groupSelect = byId("node-group");
  groupSelect.replaceChildren(new Option("Ungrouped", ""));
  (graph.groups || []).forEach((group) => groupSelect.add(new Option(group.name, group.id)));
  groupSelect.value = node?.groupId || parentNode?.groupId || "";
  const parentNote = byId("node-parent-note");
  parentNote.hidden = !parentNode;
  parentNote.textContent = parentNode ? `This node will be added under “${parentNode.name}”.` : "";
  byId("node-dialog").showModal();
  byId("node-name").focus();
}

function deleteNode(node) {
  if (!window.confirm(`Delete “${node.name}” and its relationships? Comments will remain in saved data.`)) return;
  graph.nodes = graph.nodes.filter((item) => item.id !== node.id);
  graph.nodes.forEach((item) => { if (item.parentId === node.id) item.parentId = null; });
  graph.edges = graph.edges.filter((edge) => edge.source !== node.id && edge.target !== node.id);
  selectedId = null;
  saveGraph();
  render();
}

function setProviderFields() {
  const provider = document.querySelector('input[name="provider"]:checked')?.value || "local";
  byId("deepseek-settings").hidden = provider !== "deepseek";
  byId("local-settings").hidden = provider !== "local";
  byId("provider-pill").dataset.provider = provider;
  byId("provider-pill").lastElementChild.textContent = provider === "deepseek" ? "DeepSeek API" : "Local GGUF";
}

function openSettings() {
  document.querySelector(`input[name="provider"][value="${config.provider || "local"}"]`).checked = true;
  byId("workspace-title-input").value = config.workspace_title || "Farmers' Market";
  byId("workspace-description-input").value = config.workspace_description || "";
  byId("publish-enabled").checked = Boolean(config.publish_enabled);
  byId("deepseek-model").value = config.deepseek_model || "deepseek-flash";
  byId("server-path").value = config.local_server_exe || "";
  byId("model-path").value = config.local_model_file || "";
  byId("local-port").value = config.local_port || 8080;
  byId("context-size").value = config.context_size || 4096;
  byId("gpu-layers").value = config.gpu_layers ?? 99;
  byId("deepseek-key").value = "";
  setProviderFields();
  byId("settings-dialog").showModal();
}

async function refreshServerStatus() {
  try {
    const result = await api("/api/local/diagnostics");
    const server = result.checks.find((check) => check.name === "Server health");
    byId("server-status").textContent = server?.ok ? "Running" : "Not running";
  } catch (error) {
    byId("server-status").textContent = error.message;
  }
}

async function showDiagnostics() {
  byId("diagnostics-dialog").showModal();
  await loadDiagnostics();
}

async function loadDiagnostics() {
  byId("diagnostic-checks").replaceChildren(el("p", "diagnostic-intro", "Running local checks…"));
  try {
    const report = await api("/api/local/diagnostics");
    const checks = byId("diagnostic-checks");
    checks.replaceChildren();
    report.checks.forEach((check) => {
      const row = el("div", `diagnostic-check${check.ok ? "" : " fail"}`);
      row.append(el("span", "check-icon", check.ok ? "✓" : "!"), el("b", "", check.name), el("span", "", check.detail));
      checks.append(row);
    });
    const hints = byId("diagnostic-hints");
    hints.replaceChildren();
    (report.hints || []).forEach((hint) => hints.append(el("p", "", hint)));
    byId("server-log").textContent = report.log || "No server log yet. Start the local model server to collect diagnostic output.";
  } catch (error) {
    byId("diagnostic-checks").replaceChildren(el("p", "diagnostic-intro", error.message));
  }
}

function addChatMessage(role, text) {
  const message = el("p", `chat-message ${role}`, `${role === "user" ? "You" : "Assistant"}: ${text}`);
  byId("chat-log").append(message);
  byId("chat-log").scrollTop = byId("chat-log").scrollHeight;
}

async function sendChat(message) {
  addChatMessage("user", message);
  byId("assistant-status").textContent = "Thinking…";
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        selected_node_id: selectedId,
        map_owner_id: viewedMap?.owner_id || "",
      }),
    });
    addChatMessage("assistant", result.answer);
    byId("assistant-status").textContent = "Assistant ready";
  } catch (error) {
    addChatMessage("assistant", `Could not connect: ${error.message}`);
    byId("assistant-status").textContent = "Connection issue";
  }
}

async function refreshNetworkStatus() {
  try {
    const status = await api("/api/network/status");
    const pill = byId("network-pill");
    pill.dataset.connected = String(status.connected);
    pill.dataset.published = String(status.publish_enabled);
    pill.lastElementChild.textContent = status.publish_enabled ? "Public map" : (status.connected ? "Network ready" : "Network offline");
    pill.title = `${status.status} · ${status.map_count} maps cached`;
    byId("network-credential-status").textContent = status.credentials_configured
      ? "MQTT login is saved. If offline, check the username/password or broker status."
      : "Add your MeshBook MQTT username and password here to use public discovery.";
    if (byId("network-dialog").open) byId("network-status-detail").textContent = `${status.status} · ${status.map_count} public maps currently available`;
    if (viewedMap) {
      try {
        const latest = await api(`/api/network/maps/${encodeURIComponent(viewedMap.owner_id)}`);
        viewedMap = latest;
        if (!nodeById(selectedId)) selectedId = null;
        render();
      } catch (error) {
        viewedMap = null;
        selectedId = null;
        render();
        notify(`Shared map is no longer available: ${error.message}`);
      }
    }
  } catch (error) {
    byId("network-pill").lastElementChild.textContent = "Network offline";
  }
}

async function loadNetworkResults() {
  const query = byId("network-search").value.trim();
  const results = byId("network-results");
  results.replaceChildren(el("p", "assistant-note", "Searching public maps…"));
  try {
    const response = await api(`/api/network/search?q=${encodeURIComponent(query)}`);
    byId("network-status-detail").textContent = `${response.status} · ${response.results.length} matching map${response.results.length === 1 ? "" : "s"}`;
    results.replaceChildren();
    if (!response.results.length) {
      results.append(el("p", "assistant-note", response.connected ? "No matching public maps are cached yet." : "Network is offline. Connect to search published maps."));
      return;
    }
    response.results.forEach((item) => {
      const row = el("article", "network-result");
      const details = document.createElement("div");
      details.append(el("h3", "", item.title), el("p", "", item.description), el("small", "", `${item.node_count} nodes · updated ${new Date(item.updated_at * 1000).toLocaleString()}`));
      const open = el("button", "primary-button", "Open map");
      open.type = "button";
      open.addEventListener("click", () => openNetworkMap(item.owner_id));
      row.append(details, open);
      results.append(row);
    });
  } catch (error) {
    results.replaceChildren(el("p", "assistant-note", error.message));
  }
}

async function openNetworkMap(ownerId) {
  try {
    viewedMap = await api(`/api/network/maps/${encodeURIComponent(ownerId)}`);
    selectedId = null;
    linking = false;
    byId("network-dialog").close();
    render();
  } catch (error) {
    notify(error.message);
  }
}

async function init() {
  try {
    const [loadedGraph, loadedSettings] = await Promise.all([api("/api/graph"), api("/api/settings")]);
    graph.nodes = loadedGraph.nodes || [];
    graph.edges = loadedGraph.edges || [];
    graph.groups = loadedGraph.groups || [];
    graph.comments = loadedGraph.comments || [];
    config = loadedSettings;
    byId("provider-pill").dataset.provider = config.provider || "local";
    byId("provider-pill").lastElementChild.textContent = config.provider === "deepseek" ? "DeepSeek API" : "Local GGUF";
    render();
    refreshServerStatus();
    refreshNetworkStatus();
    setInterval(refreshNetworkStatus, 15000);
  } catch (error) {
    notify(`App connection error: ${error.message}`);
  }
}

byId("add-node").addEventListener("click", () => openNodeDialog());
byId("empty-add").addEventListener("click", () => openNodeDialog());
byId("add-group").addEventListener("click", () => {
  byId("group-name").value = "";
  byId("group-dialog").showModal();
  byId("group-name").focus();
});
byId("group-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = byId("group-name").value.trim();
  if (!name) return;
  if ((graph.groups || []).some((group) => group.name.toLowerCase() === name.toLowerCase())) {
    notify("A group with that name already exists.");
    return;
  }
  graph.groups.push({ id: crypto.randomUUID(), name });
  saveGraph();
  byId("group-dialog").close();
  render();
  notify(`Group “${name}” created. Choose it when adding a node.`);
});
byId("node-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = byId("node-name").value.trim();
  if (!name) return;
  const groupId = byId("node-group").value || null;
  const values = { name, description: byId("node-description").value.trim(), groupId };
  if (editingId) Object.assign(nodeById(editingId), values);
  else {
    const parent = parentIdOnCreate ? nodeById(parentIdOnCreate) : null;
    const position = parent ? {
      x: parent.x + 260 <= 1550 ? parent.x + 260 : Math.max(30, parent.x - 260),
      y: Math.min(1180, parent.y + 100),
    } : centerPosition();
    const child = { id: crypto.randomUUID(), ...values, parentId: parent?.id || null, ...position };
    graph.nodes.push(child);
    if (parent) graph.edges.push({ id: crypto.randomUUID(), source: parent.id, target: child.id, label: "contains" });
  }
  saveGraph();
  byId("node-dialog").close();
  selectedId = editingId || graph.nodes[graph.nodes.length - 1].id;
  editingId = null;
  parentIdOnCreate = null;
  render();
});
byId("link-mode").addEventListener("click", () => {
  linking = !linking;
  linkSource = null;
  byId("link-mode").classList.toggle("active", linking);
  render();
});
byId("search").addEventListener("input", render);
byId("fit-map").addEventListener("click", () => {
  const current = activeGraph();
  if (!current.nodes.length) return;
  const minX = Math.min(...current.nodes.map((node) => node.x));
  const minY = Math.min(...current.nodes.map((node) => node.y));
  byId("board").scrollTo({ left: Math.max(0, minX - 40), top: Math.max(0, minY - 40), behavior: "smooth" });
});
byId("settings-open").addEventListener("click", openSettings);
byId("workspace-open").addEventListener("click", openSettings);
byId("save-network-credentials").addEventListener("click", async () => {
  try {
    const result = await api("/api/network/credentials", {
      method: "PUT",
      body: JSON.stringify({ username: byId("mqtt-username").value.trim(), password: byId("mqtt-password").value }),
    });
    byId("mqtt-password").value = "";
    byId("network-credential-status").textContent = result.message;
    notify(result.message);
    refreshNetworkStatus();
  } catch (error) {
    notify(error.message);
  }
});
byId("discover-open").addEventListener("click", () => {
  byId("network-dialog").showModal();
  refreshNetworkStatus();
  loadNetworkResults();
});
byId("return-local").addEventListener("click", () => {
  viewedMap = null;
  selectedId = null;
  render();
});
byId("network-search").addEventListener("input", () => {
  clearTimeout(networkSearchTimer);
  networkSearchTimer = setTimeout(loadNetworkResults, 250);
});
document.querySelectorAll('input[name="provider"]').forEach((radio) => radio.addEventListener("change", setProviderFields));
byId("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const provider = document.querySelector('input[name="provider"]:checked').value;
  const newConfig = {
    provider,
    workspace_title: byId("workspace-title-input").value.trim(),
    workspace_description: byId("workspace-description-input").value.trim(),
    publish_enabled: byId("publish-enabled").checked,
    deepseek_model: byId("deepseek-model").value.trim() || "deepseek-flash",
    local_server_exe: byId("server-path").value.trim(),
    local_model_file: byId("model-path").value.trim(),
    local_port: Number(byId("local-port").value),
    context_size: Number(byId("context-size").value),
    gpu_layers: Number(byId("gpu-layers").value),
  };
  try {
    const saved = await api("/api/settings", { method: "PUT", body: JSON.stringify(newConfig) });
    const apiKey = byId("deepseek-key").value.trim();
    if (apiKey) await api("/api/deepseek-key", { method: "PUT", body: JSON.stringify({ api_key: apiKey }) });
    config = newConfig;
    byId("settings-dialog").close();
    setProviderFields();
    render();
    notify(saved.network_error ? `Settings saved. ${saved.network_error}` : "Settings saved");
    refreshServerStatus();
    refreshNetworkStatus();
  } catch (error) {
    notify(error.message);
  }
});
byId("start-server").addEventListener("click", async () => {
  try {
    const settings = {
      local_server_exe: byId("server-path").value.trim(),
      local_model_file: byId("model-path").value.trim(),
      local_port: Number(byId("local-port").value),
      context_size: Number(byId("context-size").value),
      gpu_layers: Number(byId("gpu-layers").value),
    };
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settings) });
    const result = await api("/api/local/start", { method: "POST", body: "{}" });
    byId("server-status").textContent = result.message;
    notify(result.message);
  } catch (error) {
    byId("server-status").textContent = error.message;
    notify(error.message);
    loadDiagnostics();
  }
});
byId("stop-server").addEventListener("click", async () => {
  try {
    await api("/api/local/stop", { method: "POST", body: "{}" });
    byId("server-status").textContent = "Stopped";
  } catch (error) { notify(error.message); }
});
document.querySelectorAll("[data-browse]").forEach((button) => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    const result = await api("/api/local/browse", { method: "POST", body: JSON.stringify({ kind: button.dataset.browse }) });
    if (result.path) byId(button.dataset.browse === "server" ? "server-path" : "model-path").value = result.path;
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
  }
}));
byId("diagnostics-open").addEventListener("click", showDiagnostics);
byId("run-diagnostics").addEventListener("click", loadDiagnostics);
byId("semantic-toggle").addEventListener("click", () => {
  semanticEnabled = !semanticEnabled;
  byId("semantic-toggle").setAttribute("aria-pressed", String(semanticEnabled));
  renderInspector();
});
byId("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await sendChat(message);
});
document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog")?.close()));
init();
