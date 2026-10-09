// gherkin-copilot app. Builds the page with textContent only: step text comes
// from project source code and must never be treated as HTML.

const TOKEN = document.querySelector('meta[name="gc-token"]').content;
const $ = (id) => document.getElementById(id);
const KEYWORDS = [
  ["given", "Given", "Set the scene"],
  ["when", "When", "Do something"],
  ["then", "Then", "Check the result"],
  ["step", "Any keyword", "Works after Given, When or Then"],
];

const state = { projects: [], active: null, steps: [], query: "" };

// --- helpers --------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Gherkin-Token": TOKEN },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

let toastTimer;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 2200);
}

function stepText(step) {
  return step.segments.map((s) => ("slot" in s ? `{${s.slot}}` : s.text)).join("");
}

// --- projects -------------------------------------------------------------

function applyProjects(data) {
  state.projects = data.projects;
  state.active = data.active;
  const hasProjects = state.projects.length > 0;
  $("welcome").hidden = hasProjects;
  $("picker").hidden = !hasProjects;
  $("tabs").hidden = !hasProjects;
  $("panel-steps").hidden = !hasProjects;
  $("add-project-btn").hidden = !hasProjects;

  const select = $("project-select");
  select.replaceChildren(...state.projects.map((p) => el("option", { value: p.id }, p.name)));
  if (state.active) select.value = state.active;
}

function activeProject() {
  return state.projects.find((p) => p.id === state.active);
}

async function loadProjects() {
  applyProjects(await api("/api/projects"));
  if (state.active) await loadSteps();
}

async function switchProject(id) {
  applyProjects(await api("/api/projects/active", { id }));
  state.query = "";
  $("steps-search").value = "";
  await loadSteps();
}

function openAddDialog() {
  const form = $("add-form");
  form.reset();
  $("add-error").hidden = true;
  $("add-dialog").showModal();
  form.elements.name.focus();
}

async function submitAddDialog(event) {
  event.preventDefault();
  const form = event.target;
  const body = Object.fromEntries(new FormData(form));
  try {
    applyProjects(await api("/api/projects/add", body));
    $("add-dialog").close();
    toast(`Added ${body.name.trim()}`);
    await loadSteps();
  } catch (error) {
    $("add-error").textContent = error.message;
    $("add-error").hidden = false;
  }
}

function openInfoDialog() {
  const project = activeProject();
  if (!project) return;
  $("info-name").textContent = project.name;
  $("info-steps").textContent = project.steps;
  $("info-features").textContent = project.features;
  $("info-dialog").showModal();
}

async function removeActiveProject() {
  const project = activeProject();
  if (!project || !confirm(`Remove “${project.name}” from the list? Its files won't be touched.`)) return;
  applyProjects(await api("/api/projects/remove", { id: project.id }));
  $("info-dialog").close();
  toast(`Removed ${project.name}`);
  if (state.active) await loadSteps();
}

// --- steps catalogue --------------------------------------------------------

async function loadSteps() {
  const project = activeProject();
  if (!project) return;
  $("steps-error").hidden = true;
  try {
    const data = await api(`/api/steps?project=${encodeURIComponent(project.id)}`);
    if (project.id !== state.active) return; // switched again while loading
    state.steps = data.steps;
  } catch (error) {
    state.steps = [];
    $("steps-error").textContent = error.message;
    $("steps-error").hidden = false;
  }
  renderSteps();
}

function matchesQuery(step, words) {
  const haystack = `${step.keyword} ${stepText(step)} ${step.function} ${step.file}`.toLowerCase();
  return words.every((w) => haystack.includes(w));
}

// Fold the quotes around a slot into the chip: I add "{product}" -> I add [“product”]
function displayParts(segments) {
  const parts = segments.map((s) => ({ ...s }));
  parts.forEach((part, i) => {
    const before = parts[i - 1], after = parts[i + 1];
    if ("slot" in part && before?.text?.endsWith('"') && after?.text?.startsWith('"')) {
      before.text = before.text.slice(0, -1);
      after.text = after.text.slice(1);
      part.quoted = true;
    }
  });
  return parts.filter((p) => "slot" in p || p.text);
}

function stepCard(step) {
  const sentence = el("span", { class: "sentence" },
    ...displayParts(step.segments).map((s) => ("slot" in s
      ? el("span", { class: `slot ${s.type}`, title: s.type === "number" ? "A number" : "Any text" },
          s.quoted ? `“${s.slot}”` : s.slot)
      : s.text)));
  const copy = async () => {
    const keyword = step.keyword === "step" ? "Given" : step.keyword[0].toUpperCase() + step.keyword.slice(1);
    const text = `${keyword} ${stepText(step)}`;
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied to clipboard");
    } catch {
      toast(text);
    }
  };
  return el("li", {},
    el("button", { type: "button", class: "step-card", onclick: copy, title: "Copy this step" },
      sentence,
      el("span", { class: "where" }, `${step.file}:${step.line}`, el("span", { class: "fn" }, ` · ${step.function}()`))));
}

function renderSteps() {
  const words = state.query.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = state.steps.filter((s) => matchesQuery(s, words));
  const project = activeProject();
  $("steps-summary").textContent = project
    ? `${state.steps.length} step${state.steps.length === 1 ? "" : "s"} in ${project.name}` +
      (words.length ? ` · ${shown.length} matching` : "")
    : "";

  const groups = KEYWORDS.map(([key, label, meaning]) => {
    const items = shown.filter((s) => s.keyword === key);
    if (!items.length) return null;
    return el("section", { class: `group kw-${key}` },
      el("h2", {}, el("span", { class: "kw-pill" }, label), el("span", { class: "muted" }, ` ${meaning} · ${items.length}`)),
      el("ul", { class: "step-list" }, ...items.map(stepCard)));
  }).filter(Boolean);
  $("steps-groups").replaceChildren(...groups);

  const empty = $("steps-empty");
  empty.hidden = shown.length > 0 || !$("steps-error").hidden;
  empty.textContent = state.steps.length
    ? "No steps match your search."
    : "No step definitions found in this folder. Check the steps folder under Details.";
}

// --- wiring -----------------------------------------------------------------

$("add-project-btn").addEventListener("click", openAddDialog);
$("welcome-add-btn").addEventListener("click", openAddDialog);
$("add-cancel").addEventListener("click", () => $("add-dialog").close());
$("add-form").addEventListener("submit", submitAddDialog);
$("project-select").addEventListener("change", (e) => switchProject(e.target.value).catch((err) => toast(err.message)));
$("project-info-btn").addEventListener("click", openInfoDialog);
$("info-close").addEventListener("click", () => $("info-dialog").close());
$("info-remove").addEventListener("click", () => removeActiveProject().catch((err) => toast(err.message)));
$("steps-search").addEventListener("input", (e) => { state.query = e.target.value; renderSteps(); });
$("steps-refresh").addEventListener("click", () => loadSteps().then(() => toast("Rescanned")));
window.addEventListener("focus", () => { if (state.active) loadSteps(); }); // pick up edits made in the editor

loadProjects().catch((error) => toast(error.message));
