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
const TABS = ["build", "scenarios", "steps", "dupes", "history"];
const OUTLINE_KINDS = ["Scenario Outline", "Scenario Template"];

const state = {
  projects: [],
  active: null,
  steps: [],
  query: "",
  paletteQuery: "",
  tab: "build",
  draft: emptyDraft(),
  results: [], // server check, one per block, in order
  scenarios: null, // last /api/scenarios answer
  scenariosQuery: "",
  history: null,
  dupes: null, // last /api/dupes answer
  dupesThreshold: 0.6,
  dupesKeep: new Set(), // functions the user chose to keep
};

function emptyDraft() {
  return {
    feature: "", scenario: "", kind: "Scenario", tags: "", description: "",
    examples: [], blocks: [], editing: null, dirty: false,
  };
}

// --- helpers --------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
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
  toastTimer = setTimeout(() => { box.hidden = true; }, 2600);
}

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem(key));
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    return null;
  }
  return null;
}

const capitalize = (word) => word[0].toUpperCase() + word.slice(1);
const stepKey = (step) => `${step.file}:${step.line}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const query = (params) => new URLSearchParams({ project: state.active, ...params });

function stepText(step) {
  return step.segments.map((s) => ("slot" in s ? `{${s.slot}}` : s.text)).join("");
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

function sentence(step, makeSlot) {
  return el("span", { class: "sentence" },
    ...displayParts(step.segments).map((s) => ("slot" in s ? makeSlot(s) : s.text)));
}

function slotChip(s) {
  return el("span", { class: `slot ${s.type}`, title: s.type === "number" ? "A number" : "Any text" },
    s.quoted ? `“${s.slot}”` : s.slot);
}

async function copyText(text, done = "Copied") {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch {
    toast("Couldn't copy");
  }
}

// --- tabs -------------------------------------------------------------------

function showTab(tab) {
  state.tab = TABS.includes(tab) ? tab : "build";
  store("gc-tab", state.tab);
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  }
  for (const name of TABS) $(`panel-${name}`).hidden = !state.active || name !== state.tab;
  if (!state.active) return;
  if (state.tab === "dupes") loadDupes();
  if (state.tab === "scenarios") loadScenarios();
  if (state.tab === "history") loadHistory();
}

// --- projects -------------------------------------------------------------

function applyProjects(data) {
  const previous = state.active;
  state.projects = data.projects;
  state.active = data.active;
  const hasProjects = state.projects.length > 0;
  $("welcome").hidden = hasProjects;
  $("picker").hidden = !hasProjects;
  $("tabs").hidden = !hasProjects;
  $("add-project-btn").hidden = !hasProjects;

  const select = $("project-select");
  select.replaceChildren(...state.projects.map((p) => el("option", { value: p.id }, p.name)));
  if (state.active) select.value = state.active;
  if (state.active !== previous) {
    loadDraft();
    state.dupes = state.scenarios = state.history = null;
    state.dupesKeep = new Set();
  }
  showTab(state.tab);
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
  state.query = state.paletteQuery = state.scenariosQuery = "";
  $("steps-search").value = $("palette-search").value = $("scenarios-search").value = "";
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
  const body = Object.fromEntries(new FormData(event.target));
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

// --- author (for the history) ------------------------------------------------

function author() {
  return store("gc-author") || "";
}

function askAuthor() {
  return new Promise((resolve) => {
    const dialog = $("author-dialog");
    const form = $("author-form");
    form.elements.author.value = author();
    const done = (name) => {
      form.removeEventListener("submit", submit);
      $("author-cancel").removeEventListener("click", cancel);
      dialog.close();
      resolve(name);
    };
    const submit = (event) => {
      event.preventDefault();
      const name = form.elements.author.value.trim().replace(/\s+/g, " ").slice(0, 60);
      if (!name) return;
      store("gc-author", name);
      renderAuthor();
      done(name);
    };
    const cancel = () => done(null);
    form.addEventListener("submit", submit);
    $("author-cancel").addEventListener("click", cancel);
    dialog.showModal();
    form.elements.author.focus();
  });
}

async function ensureAuthor() {
  return author() || askAuthor();
}

function renderAuthor() {
  const name = author();
  $("author-line").textContent = name ? `Changes are recorded as ${name}` : "You'll be asked for your name on your first change";
  $("author-change").textContent = name ? "Change name" : "Set name";
}

// --- steps catalogue --------------------------------------------------------

async function loadSteps() {
  const project = activeProject();
  if (!project) return;
  $("steps-error").hidden = true;
  try {
    const data = await api(`/api/steps?${query({})}`);
    if (project.id !== state.active) return; // switched again while loading
    state.steps = data.steps;
  } catch (error) {
    state.steps = [];
    $("steps-error").textContent = error.message;
    $("steps-error").hidden = false;
  }
  renderSteps();
  renderPalette();
  scheduleCheck();
  loadDupes();
  if (state.tab === "scenarios") loadScenarios();
  if (state.tab === "history") loadHistory();
}

function matchesQuery(step, text) {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = `${step.keyword} ${stepText(step)} ${step.function} ${step.file}`.toLowerCase();
  return words.every((w) => haystack.includes(w));
}

function stepCard(step) {
  const copy = () => copyText(`${step.keyword === "step" ? "Given" : capitalize(step.keyword)} ${stepText(step)}`,
    "Copied to clipboard");
  return el("li", {},
    el("button", { type: "button", class: "step-card", onclick: copy, title: "Copy this step" },
      sentence(step, slotChip),
      el("span", { class: "where" }, `${step.file}:${step.line}`, el("span", { class: "fn" }, ` · ${step.function}()`))));
}

function groupedSteps(steps, makeItem, listClass) {
  return KEYWORDS.map(([key, label, meaning]) => {
    const items = steps.filter((s) => s.keyword === key);
    if (!items.length) return null;
    return el("section", { class: `group kw-${key}` },
      el("h2", {}, el("span", { class: "kw-pill" }, label), el("span", { class: "muted" }, ` ${meaning} · ${items.length}`)),
      el("ul", { class: listClass }, ...items.map(makeItem)));
  }).filter(Boolean);
}

function renderSteps() {
  const shown = state.steps.filter((s) => matchesQuery(s, state.query));
  const project = activeProject();
  $("steps-summary").textContent = project
    ? `${plural(state.steps.length, "step")} in ${project.name}` + (state.query.trim() ? ` · ${shown.length} matching` : "")
    : "";
  $("steps-groups").replaceChildren(...groupedSteps(shown, stepCard, "step-list"));
  const empty = $("steps-empty");
  empty.hidden = shown.length > 0 || !$("steps-error").hidden;
  empty.textContent = state.steps.length
    ? "No steps match your search."
    : "No step definitions found in this folder. Check the steps folder under Details.";
}

// --- builder: palette -------------------------------------------------------

function paletteItem(step) {
  const item = el("li", { class: "palette-item", draggable: "true", title: "Drag into your scenario" },
    sentence(step, slotChip),
    el("button", { type: "button", class: "add-btn", title: "Add to the end of the scenario",
      "aria-label": `Add “${stepText(step)}”`, onclick: () => addStepBlock(step) }, "+"));
  item.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("application/x-gc-step", stepKey(step));
    event.dataTransfer.effectAllowed = "copy";
  });
  return item;
}

function renderPalette() {
  const shown = state.steps.filter((s) => matchesQuery(s, state.paletteQuery));
  const groups = groupedSteps(shown, paletteItem, "palette-items");
  $("palette-list").replaceChildren(...(groups.length ? groups
    : [el("p", { class: "muted small" }, state.steps.length ? "No steps match." : "This project has no steps yet.")]));
}

// --- builder: the draft -----------------------------------------------------

let nextId = 1;
const newId = () => `b${Date.now().toString(36)}${nextId++}`;
const draftKey = () => `gc-draft-${state.active}`;
const isOutline = () => OUTLINE_KINDS.includes(state.draft.kind);
const isBackground = () => state.draft.kind === "Background";

function loadDraft() {
  const saved = state.active && store(draftKey());
  state.draft = saved && Array.isArray(saved.blocks) ? { ...emptyDraft(), ...saved } : emptyDraft();
  state.results = [];
  syncDraftFields();
  renderBlocks();
  renderExamples();
  scheduleCheck();
}

function syncDraftFields() {
  const d = state.draft;
  $("draft-feature").value = d.feature || "";
  $("draft-scenario").value = d.scenario || "";
  $("draft-tags").value = d.tags || "";
  $("draft-description").value = d.description || "";
  if (d.description) $("description-box").open = true;
  const kind = $("draft-kind");
  if (![...kind.options].some((o) => o.value === d.kind)) kind.append(el("option", { value: d.kind }, d.kind));
  kind.value = d.kind;
  const editing = d.editing;
  $("editing-banner").hidden = !editing;
  if (editing) $("editing-where").textContent = `${editing.file} › ${editing.original_name || "Background"}`;
  $("build-title").textContent = editing ? (isBackground() ? "Edit the Background" : "Edit a scenario") : "Build a scenario";
  $("feature-field").hidden = !!editing;
  $("scenario-field").firstChild.textContent = isBackground() ? "Background name (optional) " : "Scenario ";
  $("draft-scenario").placeholder = isBackground() ? "Usually left empty" : "e.g. Buy one product";
  $("kind-field").hidden = isBackground();
  $("tags-field").hidden = isBackground();
  $("save-btn").textContent = editing ? "Save changes…" : "Save to a feature file…";
  $("save-copy-btn").hidden = !editing || isBackground();
}

function saveDraft() {
  if (state.active) store(draftKey(), state.draft);
}

function changed() {
  state.draft.dirty = true;
  saveDraft();
  renderBlocks();
  renderExamples();
  scheduleCheck();
}

function addStepBlock(step, index = state.draft.blocks.length) {
  const block = { id: newId(), kind: "step", step, values: {}, keyword: step.keyword === "step" ? null : step.keyword };
  state.draft.blocks.splice(index, 0, block);
  changed();
  requestAnimationFrame(() => document.querySelector(`[data-block="${block.id}"] .slot-input`)?.focus());
}

function addTextBlock(line) {
  const match = line.trim().match(/^(Given|When|Then|And|But|\*)\s+(.*)$/i);
  const word = match ? match[1].toLowerCase() : null;
  const text = (match ? match[2] : line).trim();
  if (!text) return;
  const keyword = ["given", "when", "then"].includes(word) ? word : null; // And / But / none: follow the line above
  state.draft.blocks.push({ id: newId(), kind: "text", text, keyword });
  changed();
}

function moveBlock(id, to) {
  const blocks = state.draft.blocks;
  const from = blocks.findIndex((b) => b.id === id);
  if (from < 0) return;
  const [block] = blocks.splice(from, 1);
  blocks.splice(Math.max(0, Math.min(to > from ? to - 1 : to, blocks.length)), 0, block);
  changed();
}

function removeBlock(id) {
  state.draft.blocks = state.draft.blocks.filter((b) => b.id !== id);
  changed();
}

function replaceWithStep(id, step) {
  const index = state.draft.blocks.findIndex((b) => b.id === id);
  if (index < 0) return;
  const [old] = state.draft.blocks.splice(index, 1);
  addStepBlock(step, index);
  state.draft.blocks[index].attachment = old.attachment;
}

// Base keyword per block, then the label shown in Gherkin ("And" when it repeats).
function keywords() {
  let previous = "given";
  return state.draft.blocks.map((block, i) => {
    const base = block.keyword || previous;
    const label = base === previous && i > 0 ? "And" : capitalize(base);
    previous = base;
    return { base, label };
  });
}

function blockText(block) {
  if (block.kind === "text") return block.text;
  return block.step.segments.map((s) => ("slot" in s ? (block.values[s.slot] ?? "").trim() : s.text)).join("");
}

function emptySlots(block) {
  if (block.kind !== "step") return [];
  return block.step.segments.filter((s) => "slot" in s && !(block.values[s.slot] ?? "").trim()).map((s) => s.slot);
}

function badNumbers(block) {
  if (block.kind !== "step") return [];
  return block.step.segments.filter((s) => {
    const value = (block.values[s.slot] ?? "").trim();
    return s.type === "number" && value && !/^-?\d+([.,]\d+)?$/.test(value) && !/^<[^<>]+>$/.test(value);
  }).map((s) => s.slot);
}

function draftLines() {
  const kws = keywords();
  return state.draft.blocks.map((block, i) => `${kws[i].label} ${blockText(block)}`);
}

function blockStatus(block, i) {
  if (emptySlots(block).length) return "incomplete";
  if (badNumbers(block).length) return "invalid";
  return state.results[i]?.status || "checking";
}

function draftSpec() {
  const d = state.draft;
  return {
    kind: d.kind,
    name: d.scenario,
    tags: d.tags,
    description: d.description,
    steps: draftLines().map((line, i) => ({ line, attachment: d.blocks[i].attachment || [] })),
    examples: isOutline() ? d.examples : [],
  };
}

function placeholders() {
  const names = new Set();
  for (const line of draftLines()) for (const m of line.matchAll(/<([^<>]+)>/g)) names.add(m[1].trim());
  return [...names];
}

// --- builder: checking --------------------------------------------------------

let checkTimer, checkSeq = 0;
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, 250);
}

async function runCheck() {
  if (!activeProject()) return;
  const seq = ++checkSeq;
  const lines = draftLines();
  if (!lines.length) {
    state.results = [];
    renderStatus();
    return;
  }
  try {
    const examples = isOutline() ? state.draft.examples.filter((t) => t.header.length && t.header.every(Boolean)) : [];
    const data = await api("/api/check", { project: state.active, lines, examples });
    if (seq !== checkSeq) return; // a newer check is on its way
    state.results = data.results;
  } catch (error) {
    if (seq !== checkSeq) return;
    state.results = [];
    toast(error.message);
  }
  renderStatus();
}

// --- builder: rendering -------------------------------------------------------

const STATUS = {
  ok: ["✓", "Ready: this step exists"],
  missing: ["✗", "This step doesn't exist yet"],
  ambiguous: ["!", "More than one step matches this line"],
  incomplete: ["…", "Fill in the blanks"],
  invalid: ["!", "Use a number here"],
  checking: ["·", "Checking…"],
};

function slotInput(block, s) {
  const value = block.values[s.slot] ?? "";
  const input = el("input", {
    class: `slot-input ${s.type}`,
    value,
    placeholder: s.slot,
    "aria-label": s.slot,
    inputmode: s.type === "number" ? "decimal" : null,
    size: String(Math.max(value.length, s.slot.length, 3) + 1),
  });
  input.addEventListener("input", () => {
    block.values[s.slot] = input.value;
    input.size = Math.max(input.value.length, s.slot.length, 3) + 1;
    state.draft.dirty = true;
    saveDraft();
    renderStatus();
    scheduleCheck();
  });
  return s.quoted ? el("span", { class: "quoted" }, "“", input, "”") : input;
}

function attachmentView(block) {
  if (!block.attachment?.length) return null;
  const isTable = block.attachment[0].trim().startsWith("|");
  return el("div", { class: "attachment" },
    el("span", { class: "muted small" }, isTable ? "Data table (kept as is)" : "Doc string (kept as is)"),
    el("button", { type: "button", class: "icon small", title: "Remove it from this step", "aria-label": "Remove attachment",
      onclick: () => { block.attachment = []; changed(); } }, "✕"),
    el("pre", {}, block.attachment.join("\n")));
}

function blockView(block, index) {
  const handle = el("span", { class: "handle", draggable: "true", title: "Drag to reorder", "aria-hidden": "true" }, "⋮⋮");
  handle.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("application/x-gc-block", block.id);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setDragImage(handle.closest("li"), 16, 16);
  });

  let body;
  if (block.kind === "step") {
    body = sentence(block.step, (s) => slotInput(block, s));
    if (block.step.keyword === "step") {
      const choose = el("select", { class: "kw-choose", "aria-label": "Keyword" },
        ...["given", "when", "then"].map((k) => el("option", { value: k }, capitalize(k))));
      choose.value = block.keyword || "given";
      choose.addEventListener("change", () => { block.keyword = choose.value; changed(); });
      body = el("span", { class: "sentence" }, choose, " ", body);
    }
  } else {
    const input = el("input", { class: "text-input", value: block.text, "aria-label": "Step text" });
    input.addEventListener("change", () => {
      if (input.value.trim()) block.text = input.value.trim();
      changed();
    });
    body = input;
  }

  const last = state.draft.blocks.length - 1;
  return el("li", { class: "block", "data-block": block.id },
    el("div", { class: "block-row" },
      handle,
      el("span", { class: "kw-pill block-kw" }),
      body,
      el("span", { class: "status", role: "img" }),
      el("span", { class: "block-tools" },
        el("button", { type: "button", class: "icon", title: "Move up", "aria-label": "Move up", disabled: index === 0,
          onclick: () => moveBlock(block.id, index - 1) }, "↑"),
        el("button", { type: "button", class: "icon", title: "Move down", "aria-label": "Move down", disabled: index === last,
          onclick: () => moveBlock(block.id, index + 2) }, "↓"),
        el("button", { type: "button", class: "icon", title: "Remove", "aria-label": "Remove step",
          onclick: () => removeBlock(block.id) }, "✕"))),
    attachmentView(block),
    el("div", { class: "block-help" }));
}

function renderBlocks() {
  $("blocks").replaceChildren(...state.draft.blocks.map(blockView));
  $("drop-empty").hidden = state.draft.blocks.length > 0;
  renderStatus();
}

function helpFor(block, result, status) {
  if (status === "incomplete") return el("p", { class: "help-line" }, `Fill in: ${emptySlots(block).join(", ")}`);
  if (status === "invalid") return el("p", { class: "help-line" }, `Use a number for: ${badNumbers(block).join(", ")}`);
  if (status === "ambiguous") {
    return el("p", { class: "help-line" }, "Matches more than one step: ",
      result.bound.map((b) => `${b.file}:${b.line}`).join(", "), ". Reword it so only one matches.");
  }
  if (status !== "missing") return null;
  if (block.kind === "step") {
    return el("p", { class: "help-line" }, isOutline()
      ? "With the first Examples row filled in, this doesn't fit the step. Check the table values."
      : "These values don't fit the step. Check for extra quotes or spaces.");
  }
  const box = el("div", { class: "missing" });
  if (result.suggestions.length) {
    box.append(el("p", { class: "help-line" }, "Did you mean:"),
      el("div", { class: "suggestions" }, ...result.suggestions.map((m) =>
        el("button", { type: "button", class: "suggestion", title: `Use this existing step (${Math.round(m.score * 100)}% similar)`,
          onclick: () => replaceWithStep(block.id, state.steps.find((s) => stepKey(s) === stepKey(m)) || m) },
          sentence(m, slotChip)))));
  } else {
    box.append(el("p", { class: "help-line" }, "No similar step exists. It needs new code."));
  }
  box.append(el("details", { class: "stub" },
    el("summary", {}, "Code for this new step"),
    el("pre", {}, result.stub),
    el("button", { type: "button", class: "ghost small", onclick: () => copyText(result.stub, "Code copied") }, "Copy code")));
  return box;
}

function renderStatus() {
  const kws = keywords();
  const counts = { ok: 0, missing: 0, other: 0 };
  state.draft.blocks.forEach((block, i) => {
    const li = document.querySelector(`[data-block="${block.id}"]`);
    if (!li) return;
    const status = blockStatus(block, i);
    const result = state.results[i];
    li.className = `block kw-${kws[i].base} status-${status}`;
    li.querySelector(".block-kw").textContent = kws[i].label;
    const [icon, label] = STATUS[status];
    const badge = li.querySelector(".status");
    badge.textContent = icon;
    badge.title = status === "ok" && result?.bound[0] ? `${label}: ${result.bound[0].file}:${result.bound[0].line}` : label;
    badge.setAttribute("aria-label", badge.title);
    li.querySelector(".block-help").replaceChildren(
      ...[result || status !== "missing" ? helpFor(block, result, status) : null].filter(Boolean));
    if (status === "ok") counts.ok += 1;
    else if (status === "missing") counts.missing += 1;
    else counts.other += 1;
  });

  const total = state.draft.blocks.length;
  const summary = $("draft-summary");
  summary.className = `summary ${!total ? "" : counts.ok === total ? "good" : counts.missing ? "bad" : "warn"}`;
  summary.textContent = !total ? "Your scenario will appear here."
    : counts.ok === total ? (total === 1 ? "The step is ready to run." : `All ${total} steps are ready to run.`)
    : `${counts.ok} of ${total} steps ready` +
      (counts.missing ? ` · ${counts.missing} don't exist yet` : "") +
      (counts.other ? ` · ${counts.other} to fix` : "");
  renderGherkin();
  const missingTyped = state.draft.blocks.some((b, i) => b.kind === "text" && blockStatus(b, i) === "missing");
  $("stubs-btn").disabled = !missingTyped;
  $("stubs-btn").title = missingTyped ? "" : "Every typed step already exists";
  $("save-btn").disabled = !total;
  $("save-copy-btn").disabled = !total;
}

function tableLines(table, indent) {
  const rows = [table.header, ...table.rows];
  const widths = table.header.map((_, c) => Math.max(...rows.map((r) => (r[c] ?? "").length)));
  return rows.map((r) => `${indent}| ${table.header.map((_, c) => (r[c] ?? "").padEnd(widths[c])).join(" | ")} |`);
}

function gherkinLines() {
  const d = state.draft;
  const head = [];
  if (!d.editing) head.push(`Feature: ${d.feature.trim() || "Untitled feature"}`, "");
  if (d.tags.trim() && !isBackground()) head.push(`  ${d.tags.trim()}`);
  const name = d.scenario.trim() || (isBackground() ? "" : "Untitled scenario");
  head.push(`  ${d.kind}: ${name}`.trimEnd());
  for (const line of d.description.split("\n").map((l) => l.trim()).filter(Boolean)) head.push(`    ${line}`);
  const tail = [];
  if (isOutline()) {
    for (const table of d.examples) {
      tail.push("", `    Examples:${table.name ? ` ${table.name}` : ""}`, ...tableLines(table, "      "));
    }
  }
  return { head, tail };
}

function gherkinText() {
  const { head, tail } = gherkinLines();
  const steps = draftLines().flatMap((l, i) => [`    ${l}`, ...(state.draft.blocks[i].attachment || []).map((a) => `    ${a}`)]);
  return [...head, ...steps, ...tail].join("\n");
}

function renderGherkin() {
  const lines = draftLines();
  const pre = $("gherkin");
  if (!lines.length) {
    pre.replaceChildren(el("span", { class: "muted" }, "Feature: …\n\n  Scenario: …\n    Given …"));
    return;
  }
  const { head, tail } = gherkinLines();
  pre.replaceChildren(head.join("\n") + "\n",
    ...lines.flatMap((line, i) => [
      el("span", { class: `line status-${blockStatus(state.draft.blocks[i], i)}` }, `    ${line}\n`),
      ...(state.draft.blocks[i].attachment || []).map((a) => el("span", { class: "line muted" }, `    ${a}\n`)),
    ]),
    tail.length ? el("span", { class: "table-lines" }, tail.join("\n") + "\n") : "");
}

// --- builder: Examples tables (Scenario Outline) ------------------------------------

function newTable() {
  const names = placeholders();
  const header = names.length ? names : ["value"];
  return { name: "", tags: [], header, rows: [header.map(() => "")] };
}

function cellInput(value, label, onInput) {
  const input = el("input", { class: "cell", value, "aria-label": label, size: String(Math.max(value.length, 6)) });
  input.addEventListener("input", () => {
    onInput(input.value);
    input.size = Math.max(input.value.length, 6);
    state.draft.dirty = true;
    saveDraft();
    renderGherkin();
    scheduleCheck();
  });
  return input;
}

function renderExamples() {
  const box = $("examples");
  box.hidden = !isOutline();
  if (!isOutline()) return box.replaceChildren();
  const d = state.draft;
  if (!d.examples.length) d.examples.push(newTable());
  const used = placeholders();
  box.replaceChildren(
    el("div", { class: "examples-head" },
      el("h2", {}, "Examples"),
      el("span", { class: "muted small" }, "The scenario runs once for each row. Use ", el("code", {}, "<column>"),
        " in a step to put that column's value there.")),
    ...d.examples.map((table, t) => {
      const missingCols = used.filter((name) => !table.header.includes(name));
      const structural = (fn) => () => { fn(); changed(); };
      return el("div", { class: "examples-table" },
        d.examples.length > 1 || table.name
          ? cellInput(table.name, "Examples name", (v) => { table.name = v; }) : null,
        el("div", { class: "table-scroll" },
          el("table", {},
            el("thead", {}, el("tr", {},
              ...table.header.map((h, c) => el("th", {},
                cellInput(h, `Column ${c + 1} name`, (v) => { table.header[c] = v.trim(); }),
                table.header.length > 1 ? el("button", { type: "button", class: "icon small", title: "Remove column",
                  "aria-label": `Remove column ${h}`,
                  onclick: structural(() => { table.header.splice(c, 1); table.rows.forEach((r) => r.splice(c, 1)); }) }, "✕") : null)),
              el("th", {}))),
            el("tbody", {}, ...table.rows.map((row, r) => el("tr", {},
              ...table.header.map((h, c) => el("td", {}, cellInput(row[c] ?? "", `${h}, row ${r + 1}`, (v) => { row[c] = v; }))),
              el("td", {}, el("button", { type: "button", class: "icon small", title: "Remove row", "aria-label": `Remove row ${r + 1}`,
                disabled: table.rows.length === 1, onclick: structural(() => table.rows.splice(r, 1)) }, "✕"))))))),
        el("div", { class: "examples-tools" },
          el("button", { type: "button", class: "ghost small", onclick: structural(() => table.rows.push(table.header.map(() => ""))) }, "+ Row"),
          el("button", { type: "button", class: "ghost small", onclick: structural(() => {
            let name = "column", n = 2;
            while (table.header.includes(name)) name = `column${n++}`;
            table.header.push(name);
            table.rows.forEach((r) => r.push(""));
          }) }, "+ Column"),
          ...missingCols.map((name) => el("button", { type: "button", class: "ghost small warn", title: `Steps use <${name}> but there's no column for it`,
            onclick: structural(() => { table.header.push(name); table.rows.forEach((r) => r.push("")); }) }, `+ Column for <${name}>`)),
          d.examples.length > 1 ? el("button", { type: "button", class: "ghost small", onclick: structural(() => d.examples.splice(t, 1)) },
            "Remove this table") : null));
    }),
    el("button", { type: "button", class: "ghost small", onclick: () => { d.examples.push(newTable()); changed(); } }, "+ Another Examples table"));
}

// --- builder: drag and drop -------------------------------------------------

function dropIndex(event) {
  const items = [...$("blocks").children];
  const i = items.findIndex((li) => {
    const box = li.getBoundingClientRect();
    return event.clientY < box.top + box.height / 2;
  });
  return i < 0 ? items.length : i;
}

function clearDropMarks() {
  for (const li of $("blocks").children) li.classList.remove("drop-before", "drop-after");
  $("drop-empty").classList.remove("drop-over");
}

function onDragOver(event) {
  const types = event.dataTransfer.types;
  if (!types.includes("application/x-gc-step") && !types.includes("application/x-gc-block")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = types.includes("application/x-gc-block") ? "move" : "copy";
  clearDropMarks();
  const items = [...$("blocks").children];
  const at = dropIndex(event);
  if (!items.length) $("drop-empty").classList.add("drop-over");
  else if (at < items.length) items[at].classList.add("drop-before");
  else items[items.length - 1].classList.add("drop-after");
}

function onDrop(event) {
  event.preventDefault();
  const at = dropIndex(event);
  clearDropMarks();
  const blockId = event.dataTransfer.getData("application/x-gc-block");
  if (blockId) return moveBlock(blockId, at);
  const key = event.dataTransfer.getData("application/x-gc-step");
  const step = state.steps.find((s) => stepKey(s) === key);
  if (step) addStepBlock(step, at);
}

// --- scenarios list -----------------------------------------------------------------

async function loadScenarios() {
  if (!state.active) return;
  try {
    state.scenarios = await api(`/api/scenarios?${query({})}`);
    $("scenarios-error").hidden = true;
  } catch (error) {
    state.scenarios = null;
    $("scenarios-error").textContent = error.message;
    $("scenarios-error").hidden = false;
  }
  renderScenarios();
}

function blockMatches(file, block, words) {
  const haystack = `${file.file} ${file.feature} ${block.kind} ${block.name} ${block.tags.join(" ")}`.toLowerCase();
  return words.every((w) => haystack.includes(w));
}

function scenarioRow(file, block) {
  const where = { file: file.file, start: block.start, name: block.name };
  const status = block.missing
    ? el("span", { class: "pill bad", title: "Steps without a definition" }, `✗ ${block.missing} missing`)
    : block.ambiguous
      ? el("span", { class: "pill warn" }, `! ${block.ambiguous} ambiguous`)
      : el("span", { class: "pill good" }, "✓ ready");
  const kind = block.kind === "Background" ? "Background" : OUTLINE_KINDS.includes(block.kind) ? "Outline" : null;
  return el("li", { class: "scenario-row" },
    el("div", { class: "scenario-main" },
      el("div", { class: "scenario-name" },
        kind ? el("span", { class: `kind ${kind.toLowerCase()}` }, kind) : null,
        el("strong", {}, block.name || (block.kind === "Background" ? "Runs before every scenario in this file" : "(no name)"))),
      el("div", { class: "scenario-meta" },
        ...block.tags.map((t) => el("span", { class: "tag" }, t)),
        el("span", { class: "muted small" }, plural(block.steps, "step") +
          (block.examples ? ` · ${plural(block.examples, "example row")}` : "") + ` · line ${block.start}`),
        status)),
    el("div", { class: "scenario-actions" },
      el("button", { type: "button", class: "primary small", onclick: () => openForEdit(where) }, "Edit"),
      block.kind !== "Background" ? el("button", { type: "button", class: "ghost small",
        onclick: () => directChange("duplicate", "/api/scenario/duplicate", where, `Duplicate “${block.name}”`) }, "Duplicate") : null,
      el("button", { type: "button", class: "ghost small danger-text",
        onclick: () => directChange("delete", "/api/scenario/delete", where, `Delete “${block.name || "Background"}”`) }, "Delete")));
}

function renderScenarios() {
  const data = state.scenarios;
  if (!data) return;
  const words = state.scenariosQuery.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  const cards = data.files.map((file) => {
    const blocks = file.blocks.filter((b) => blockMatches(file, b, words));
    total += file.blocks.filter((b) => b.kind !== "Background").length;
    if (!blocks.length && words.length) return null;
    return el("article", { class: "feature-card" },
      el("header", {},
        el("div", {}, el("h2", {}, file.feature || file.file),
          el("span", { class: "where" }, file.file), ...file.tags.map((t) => el("span", { class: "tag" }, t))),
        el("button", { type: "button", class: "ghost small", onclick: () => newScenarioIn(file) }, "+ New scenario here")),
      blocks.length ? el("ul", { class: "scenario-rows" }, ...blocks.map((b) => scenarioRow(file, b)))
        : el("p", { class: "muted small" }, "No scenarios yet."));
  }).filter(Boolean);
  $("scenarios-summary").textContent = `${plural(total, "scenario")} in ${plural(data.files.length, "feature file")}`;
  $("scenarios-list").replaceChildren(...(cards.length ? cards : [el("p", { class: "muted center" },
    data.files.length ? "Nothing matches your search." : "No .feature files in this project's features folder yet.")]));
}

function confirmDiscard() {
  const d = state.draft;
  return !d.dirty || !d.blocks.length || confirm("Your current scenario has unsaved changes. Discard them?");
}

async function openForEdit(where) {
  if (!confirmDiscard()) return;
  let data;
  try {
    data = await api(`/api/scenario/open?${query({ file: where.file, start: where.start, name: where.name })}`);
  } catch (error) {
    return toast(error.message);
  }
  state.draft = {
    ...emptyDraft(),
    feature: data.feature,
    scenario: data.name,
    kind: data.kind,
    tags: data.tags.join(" "),
    description: data.description,
    examples: data.examples,
    editing: { file: data.file, start: data.start, original_name: data.original_name },
    blocks: data.steps.map((s) => (s.step
      ? { id: newId(), kind: "step", step: s.step, values: s.values,
          keyword: s.step.keyword === "step" ? s.keyword : s.step.keyword, attachment: s.attachment }
      : { id: newId(), kind: "text", text: s.text, keyword: s.keyword, attachment: s.attachment })),
  };
  state.results = [];
  saveDraft();
  syncDraftFields();
  renderBlocks();
  renderExamples();
  scheduleCheck();
  showTab("build");
}

function newScenarioIn(file) {
  if (!confirmDiscard()) return;
  state.draft = { ...emptyDraft(), feature: file.feature, saveTo: file.file };
  state.results = [];
  saveDraft();
  syncDraftFields();
  renderBlocks();
  renderExamples();
  showTab("build");
  toast(`It will be saved to ${file.file}`);
}

function stopEditing() {
  if (!confirmDiscard()) return;
  state.draft = emptyDraft();
  state.results = [];
  saveDraft();
  syncDraftFields();
  renderBlocks();
  renderExamples();
}

// --- history ----------------------------------------------------------------------

const ACTIONS = {
  create: "Created file", add: "Added scenario", edit: "Edited", delete: "Deleted", duplicate: "Duplicated",
  restore: "Restored", merge: "Merged duplicates", "add steps": "Added step code",
};

async function loadHistory() {
  if (!state.active) return;
  try {
    state.history = await api(`/api/history?${query({})}`);
    $("history-error").hidden = true;
  } catch (error) {
    state.history = null;
    $("history-error").textContent = error.message;
    $("history-error").hidden = false;
  }
  renderHistory();
}

function when(iso) {
  const date = new Date(iso);
  const seconds = (Date.now() - date) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function diffView(lines) {
  return el("pre", { class: "diff" }, ...lines.map((line) => el("span", {
    class: line.startsWith("+++") || line.startsWith("---") ? "file"
      : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "",
  }, `${line}\n`)));
}

function renderHistory() {
  renderAuthor();
  const data = state.history;
  if (!data) return;
  $("history-hint").textContent = `Every change made in this app is recorded in ${data.file}. ` +
    "Commit that file with your tests and your team sees the same history.";
  $("history-summary").textContent = data.entries.length ? plural(data.entries.length, "change") : "";
  $("history-list").replaceChildren(...(data.entries.length ? data.entries.map((e) => {
    const restorable = e.before != null || e.after != null;
    return el("li", { class: `history-entry action-${e.action.replace(/\s/g, "-")}` },
      el("details", {},
        el("summary", {},
          el("span", { class: "action" }, ACTIONS[e.action] || e.action),
          el("span", { class: "what" }, e.scenario ? `${e.file} › ${e.scenario}` : e.files.join(", ")),
          el("span", { class: "who muted small", title: new Date(e.time).toLocaleString() },
            `${e.author || "someone"} · ${when(e.time)}`)),
        diffView(e.diff),
        restorable ? el("button", { type: "button", class: "ghost small",
          onclick: () => directChange("restore", "/api/history/restore", { id: e.id },
            e.action === "delete" ? "Bring back this scenario" : "Restore the version from before this change") },
          e.action === "delete" ? "Bring it back…" : e.action === "add" || e.action === "create" || e.action === "duplicate"
            ? "Undo (remove it)…" : "Restore the earlier version…") : null));
  }) : [el("li", { class: "muted center" }, "No changes yet. Edits, new scenarios and merges made here will appear in this list.")]));
}

// --- change dialog: every write is previewed first --------------------------------------

const dialog = { kind: null, preview: null, request: null };
const SUBMIT = { save: "Save", copy: "Save", stubs: "Add to file", merge: "Merge", edit: "Save changes",
  delete: "Delete", duplicate: "Duplicate", restore: "Restore" };

function slug(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
}

function radio(name, value, label, checked) {
  return el("label", { class: "radio" }, el("input", { type: "radio", name, value, checked }), label);
}

function draftBlocked() {
  if (state.draft.blocks.some((b, i) => ["incomplete", "invalid"].includes(blockStatus(b, i)))) {
    toast("Fill in the blanks first");
    return true;
  }
  if (!isBackground() && !state.draft.scenario.trim()) {
    toast("Give the scenario a name first");
    $("draft-scenario").focus();
    return true;
  }
  return false;
}

async function openChangeDialog(kind) {
  if (!activeProject()) return;
  if ((kind === "save" || kind === "copy") && draftBlocked()) return;
  if (kind === "save" && state.draft.editing) {
    const e = state.draft.editing;
    return directChange("edit", "/api/scenario/edit", { file: e.file, start: e.start, name: e.original_name, spec: draftSpec() },
      `Save changes to “${state.draft.scenario || "Background"}”`);
  }
  let files;
  try {
    files = await api(`/api/files?${query({})}`);
  } catch (error) {
    return toast(error.message);
  }
  dialog.kind = kind;
  dialog.preview = null;
  const options = $("change-options");
  if (kind === "save" || kind === "copy") {
    $("change-title").textContent = kind === "copy" ? "Save as a new scenario" : "Save scenario";
    const target = state.draft.saveTo || state.draft.editing?.file;
    const existing = el("select", { name: "existing", "aria-label": "Feature file" },
      ...files.features.map((f) => el("option", { value: f, selected: f === target }, f)));
    const append = !!target && files.features.includes(target);
    options.replaceChildren(
      el("div", { class: "radios" },
        radio("mode", "new", "Create a new feature file", !append),
        radio("mode", "append", "Add to an existing file", append)),
      el("label", { class: "mode-new" }, "File name",
        el("span", { class: "with-suffix" },
          el("input", { name: "file", value: slug(state.draft.feature) || "new_feature", autocomplete: "off" }),
          el("span", { class: "suffix" }, ".feature"))),
      el("label", { class: "mode-append" }, "Feature file", existing));
    if (!files.features.length) options.querySelector('input[value="append"]').disabled = true;
    const missing = state.draft.blocks.filter((b, i) => blockStatus(b, i) !== "ok").length;
    $("change-note").textContent = missing
      ? `${plural(missing, "step")} ${missing === 1 ? "doesn't" : "don't"} exist yet. You can still save; the scenario will fail until ${missing === 1 ? "it's" : "they're"} written.`
      : `It goes into ${activeProject().features}.`;
  } else {
    $("change-title").textContent = "Add missing steps to code";
    options.replaceChildren(
      el("label", {}, "Steps file", el("select", { name: "file", "aria-label": "Steps file" },
        ...files.steps.map((f) => el("option", { value: f }, f)))));
    $("change-note").textContent = "Each new step is added as a template that fails with “Step not written yet” " +
      "until a developer fills in the code. Steps you built from blocks already exist, so only typed steps are added.";
  }
  syncModes();
  showPreview(null);
  $("change-dialog").showModal();
}

async function directChange(kind, base, body, title) {
  dialog.kind = kind;
  dialog.request = [base, { project: state.active, ...body }];
  try {
    const preview = await api(`${base}/preview`, dialog.request[1]);
    $("change-options").replaceChildren();
    showPreview(preview);
    $("change-title").textContent = title;
    $("change-back").hidden = true;
    $("change-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

function syncModes() {
  const mode = $("change-form").elements.mode?.value;
  for (const node of document.querySelectorAll(".mode-new")) node.hidden = mode !== "new";
  for (const node of document.querySelectorAll(".mode-append")) node.hidden = mode !== "append";
}

function showPreview(preview) {
  dialog.preview = preview;
  $("change-error").hidden = true;
  $("change-options").hidden = !!preview;
  $("change-note").hidden = !!preview;
  $("change-preview").hidden = !preview;
  $("change-back").hidden = !preview;
  $("change-submit").textContent = preview ? SUBMIT[dialog.kind] : "Preview";
  $("change-submit").classList.toggle("danger-fill", !!preview && dialog.kind === "delete");
  if (preview) {
    const files = preview.files || [preview];
    $("change-diff").replaceChildren(...files.flatMap((file) => [
      files.length > 1 ? el("span", { class: "file" }, `${file.file}\n`) : null,
      ...file.diff.slice(2).map((line) =>
        el("span", { class: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "" },
          `${line}\n`)),
    ].filter(Boolean)));
    if (dialog.kind === "merge") $("change-title").textContent = `Merge: change ${plural(files.length, "file")}`;
    else if (["save", "copy", "stubs"].includes(dialog.kind)) {
      $("change-title").textContent = preview.creates ? `Create ${preview.file}` : `Change ${preview.file}`;
    }
  }
}

function changeRequest() {
  const form = $("change-form").elements;
  const project = state.active;
  if (dialog.kind === "save" || dialog.kind === "copy") {
    const mode = form.mode.value;
    return ["/api/scenario", { project, mode, file: mode === "new" ? form.file.value : form.existing.value,
      feature: state.draft.feature, spec: draftSpec() }];
  }
  const only = state.draft.blocks.map((b, i) => (b.kind === "text" && blockStatus(b, i) === "missing" ? i : -1))
    .filter((i) => i >= 0);
  return ["/api/stubs", { project, file: form.file.value, lines: draftLines(), only,
    examples: isOutline() ? state.draft.examples : [] }];
}

async function submitChange(event) {
  event.preventDefault();
  try {
    if (!dialog.preview) {
      dialog.request = changeRequest();
      showPreview(await api(`${dialog.request[0]}/preview`, dialog.request[1]));
      return;
    }
    const name = await ensureAuthor();
    if (!name) return;
    const [base, body] = dialog.request;
    const result = await api(`${base}/save`, { ...body, hash: dialog.preview.hash, author: name });
    $("change-dialog").close();
    afterSave(dialog.kind, result, body);
  } catch (error) {
    $("change-error").textContent = error.message;
    $("change-error").hidden = false;
  }
}

function afterSave(kind, result, body) {
  const saved = Array.isArray(result.saved) ? result.saved.join(", ") : result.saved;
  toast({ save: `Saved to ${saved}`, copy: `Saved to ${saved}`, merge: `Merged: updated ${saved}`, stubs: `Added steps to ${saved}`,
    edit: `Saved changes to ${saved}`, delete: `Deleted from ${saved}`, duplicate: `Duplicated in ${saved}`,
    restore: `Restored in ${saved}` }[kind]);
  const d = state.draft;
  if (kind === "edit") {
    d.editing.original_name = d.scenario.trim().replace(/\s+/g, " ");
    d.dirty = false;
    syncDraftFields();
  }
  if ((kind === "save" || kind === "copy") && !d.editing) d.dirty = false;
  if (kind === "delete" && d.editing && d.editing.file === body.file && d.editing.start === body.start) {
    d.editing = null;
    d.dirty = true;
    syncDraftFields();
  }
  saveDraft();
  loadSteps();
}

// --- duplicates ----------------------------------------------------------------

let dupesSeq = 0;
async function loadDupes() {
  if (!state.active) return;
  const seq = ++dupesSeq;
  const params = query({ threshold: state.dupesThreshold });
  for (const fn of state.dupesKeep) params.append("keep", fn);
  try {
    const data = await api(`/api/dupes?${params}`);
    if (seq !== dupesSeq) return;
    state.dupes = data;
    $("dupes-error").hidden = true;
  } catch (error) {
    if (seq !== dupesSeq) return;
    state.dupes = null;
    $("dupes-error").textContent = error.message;
    $("dupes-error").hidden = false;
  }
  renderDupes();
}

function uses(n) {
  return n === 0 ? "not used in any feature file" : `used in ${plural(n, "feature line")}`;
}

function dupeSide(step, role, pair) {
  const keyword = step.keyword === "step" ? "Any" : capitalize(step.keyword);
  return el("div", { class: `dupe-side ${role}` },
    el("span", { class: "side-label" }, role === "keep" ? "Keep" : "Remove"),
    el("span", { class: "sentence" }, el("strong", { class: "dupe-kw" }, `${keyword} `),
      ...sentence(step, slotChip).childNodes),
    el("span", { class: "where" }, `${step.file}:${step.line} · ${step.function}()`),
    el("span", { class: "uses" }, uses(step.uses)),
    role === "drop" ? el("button", { type: "button", class: "ghost small", onclick: () => {
      state.dupesKeep.delete(pair.keep.function);
      state.dupesKeep.add(step.function);
      loadDupes();
    } }, "Keep this one instead") : null);
}

function dupeCard(pair) {
  const footer = pair.ok
    ? el("div", { class: "dupe-foot" },
        el("span", {}, pair.rewrites
          ? `Merging rewrites ${plural(pair.rewrites, "feature line")} and removes ${pair.drop.function}().`
          : `No feature file uses the second wording, so merging just removes ${pair.drop.function}().`),
        el("button", { type: "button", class: "primary", onclick: () => directChange("merge", "/api/merge",
          { threshold: state.dupesThreshold, pair: pair.id, keep: [...state.dupesKeep],
            label: `${pair.keep.function}() kept, ${pair.drop.function}() removed` }, "Merge") }, "Preview merge…"))
    : el("div", { class: "dupe-foot problems" },
        el("strong", {}, "Can't merge automatically"),
        el("ul", {}, ...pair.problems.map((p) => el("li", {}, p))),
        el("span", { class: "muted small" }, "Tidy these up by hand, or reword one step so they're no longer alike."));
  return el("article", { class: `dupe ${pair.ok ? "" : "blocked"}` },
    el("div", { class: "dupe-head" }, el("span", { class: "score" }, `${Math.round(pair.score * 100)}% similar`)),
    el("div", { class: "dupe-sides" }, dupeSide(pair.keep, "keep", pair), el("span", { class: "arrow", "aria-hidden": "true" }, "←"),
      dupeSide(pair.drop, "drop", pair)),
    footer);
}

function renderDupes() {
  const data = state.dupes;
  const count = $("dupes-count");
  count.hidden = !data?.pairs.length;
  count.textContent = data?.pairs.length || "";
  if (!data) return;
  const n = data.pairs.length;
  $("dupes-summary").textContent = n ? `${plural(n, "pair")} of steps say the same thing in different words` : "";
  const list = data.pairs.map(dupeCard);
  if (data.skipped.length) {
    list.push(el("p", { class: "muted small" },
      `${plural(data.skipped.length, "more pair")} share a step with one above. Merge those first and the rest will show up here.`));
  }
  $("dupes-list").replaceChildren(...(list.length ? list : [
    el("div", { class: "all-clear" }, el("strong", {}, "No duplicates found."),
      el("span", { class: "muted" }, " Every step says something different. Slide the similarity lower to look harder.")),
  ]));
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
for (const button of document.querySelectorAll(".tab")) button.addEventListener("click", () => showTab(button.dataset.tab));

$("palette-search").addEventListener("input", (e) => { state.paletteQuery = e.target.value; renderPalette(); });
for (const [id, key] of [["draft-feature", "feature"], ["draft-scenario", "scenario"], ["draft-tags", "tags"],
  ["draft-description", "description"]]) {
  $(id).addEventListener("input", (e) => {
    state.draft[key] = e.target.value;
    state.draft.dirty = true;
    saveDraft();
    renderGherkin();
  });
}
$("draft-kind").addEventListener("change", (e) => {
  state.draft.kind = e.target.value;
  if (isOutline() && !state.draft.examples.length) state.draft.examples.push(newTable());
  syncDraftFields();
  changed();
});
$("typed-form").addEventListener("submit", (e) => {
  e.preventDefault();
  addTextBlock($("typed-input").value);
  $("typed-input").value = "";
});
$("build-clear").addEventListener("click", () => {
  if (state.draft.blocks.length && !confirm(state.draft.editing
    ? "Stop editing and start a new scenario? Unsaved changes are lost."
    : "Clear this scenario and start again?")) return;
  state.draft = emptyDraft();
  syncDraftFields();
  changed();
  state.draft.dirty = false;
});
$("editing-stop").addEventListener("click", stopEditing);
$("copy-gherkin").addEventListener("click", () => copyText(gherkinText(), "Gherkin copied"));
for (const zone of [$("blocks"), $("drop-empty")]) {
  zone.addEventListener("dragover", onDragOver);
  zone.addEventListener("drop", onDrop);
  zone.addEventListener("dragleave", (e) => { if (!zone.contains(e.relatedTarget)) clearDropMarks(); });
}
document.addEventListener("dragend", clearDropMarks);

$("save-btn").addEventListener("click", () => openChangeDialog("save"));
$("save-copy-btn").addEventListener("click", () => openChangeDialog("copy"));
$("stubs-btn").addEventListener("click", () => openChangeDialog("stubs"));
$("change-form").addEventListener("submit", submitChange);
$("change-options").addEventListener("change", syncModes);
$("change-back").addEventListener("click", () => showPreview(null));
$("change-cancel").addEventListener("click", () => $("change-dialog").close());

$("scenarios-search").addEventListener("input", (e) => { state.scenariosQuery = e.target.value; renderScenarios(); });
$("author-change").addEventListener("click", () => askAuthor());
$("dupes-threshold").addEventListener("input", (e) => {
  state.dupesThreshold = Number(e.target.value);
  $("dupes-threshold-value").textContent = `${Math.round(state.dupesThreshold * 100)}%`;
  loadDupes();
});

window.addEventListener("focus", () => { if (state.active) loadSteps(); }); // pick up edits made in the editor

state.tab = store("gc-tab") || "build";
renderAuthor();
loadProjects().catch((error) => toast(error.message));
