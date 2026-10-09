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
const TABS = ["build", "steps", "dupes"];

const state = {
  projects: [],
  active: null,
  steps: [],
  query: "",
  paletteQuery: "",
  tab: "build",
  draft: emptyDraft(),
  dupes: null, // last /api/dupes answer
  dupesThreshold: 0.6,
  dupesKeep: new Set(), // functions the user chose to keep
  results: [], // server check, one per block, in order
  checking: false,
};

function emptyDraft() {
  return { feature: "", scenario: "", blocks: [] };
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
  toastTimer = setTimeout(() => { box.hidden = true; }, 2400);
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

// --- tabs -------------------------------------------------------------------

function showTab(tab) {
  state.tab = TABS.includes(tab) ? tab : "build";
  store("gc-tab", state.tab);
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  }
  for (const name of TABS) $(`panel-${name}`).hidden = !state.active || name !== state.tab;
  if (state.tab === "dupes" && state.active) loadDupes();
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
    state.dupes = null;
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
  state.query = state.paletteQuery = "";
  $("steps-search").value = $("palette-search").value = "";
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
  renderPalette();
  scheduleCheck();
  loadDupes();
}

function matchesQuery(step, query) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = `${step.keyword} ${stepText(step)} ${step.function} ${step.file}`.toLowerCase();
  return words.every((w) => haystack.includes(w));
}

function stepCard(step) {
  const copy = async () => {
    const text = `${step.keyword === "step" ? "Given" : capitalize(step.keyword)} ${stepText(step)}`;
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied to clipboard");
    } catch {
      toast(text);
    }
  };
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
    ? `${state.steps.length} step${state.steps.length === 1 ? "" : "s"} in ${project.name}` +
      (state.query.trim() ? ` · ${shown.length} matching` : "")
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

function draftKey() {
  return `gc-draft-${state.active}`;
}

function loadDraft() {
  const saved = state.active && store(draftKey());
  state.draft = saved && Array.isArray(saved.blocks) ? saved : emptyDraft();
  state.results = [];
  $("draft-feature").value = state.draft.feature || "";
  $("draft-scenario").value = state.draft.scenario || "";
  renderBlocks();
  scheduleCheck();
}

function saveDraft() {
  if (state.active) store(draftKey(), state.draft);
}

function changed() {
  saveDraft();
  renderBlocks();
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
  state.draft.blocks.splice(index, 1);
  addStepBlock(step, index);
}

// Base keyword per block, then the label shown in Gherkin ("And" when it repeats).
function keywords() {
  let previous = "given";
  return state.draft.blocks.map((block) => {
    const base = block.keyword || previous;
    const label = base === previous && block !== state.draft.blocks[0] ? "And" : capitalize(base);
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
  return block.step.segments.filter((s) => s.type === "number" && (block.values[s.slot] ?? "").trim()
    && !/^-?\d+([.,]\d+)?$/.test(block.values[s.slot].trim())).map((s) => s.slot);
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

// --- builder: checking --------------------------------------------------------

let checkTimer, checkSeq = 0;
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, 250);
}

async function runCheck() {
  const project = activeProject();
  if (!project) return;
  const seq = ++checkSeq;
  const lines = draftLines();
  if (!lines.length) {
    state.results = [];
    renderStatus();
    return;
  }
  try {
    const data = await api("/api/check", { project: project.id, lines });
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
    saveDraft();
    renderStatus();
    scheduleCheck();
  });
  return s.quoted ? el("span", { class: "quoted" }, "“", input, "”") : input;
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
    el("div", { class: "block-help" }));
}

function renderBlocks() {
  const list = $("blocks");
  list.replaceChildren(...state.draft.blocks.map(blockView));
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
    return el("p", { class: "help-line" }, "These values don't fit the step. Check for extra quotes or spaces.");
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
  const copy = () => navigator.clipboard.writeText(result.stub).then(() => toast("Code copied"), () => toast("Couldn't copy"));
  box.append(el("details", { class: "stub" },
    el("summary", {}, "Code for this new step"),
    el("pre", {}, result.stub),
    el("button", { type: "button", class: "ghost small", onclick: copy }, "Copy code")));
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
    li.querySelector(".block-help").replaceChildren(...[result || status !== "missing" ? helpFor(block, result, status) : null]
      .filter(Boolean));
    if (status === "ok") counts.ok += 1;
    else if (status === "missing") counts.missing += 1;
    else counts.other += 1;
  });

  const total = state.draft.blocks.length;
  const summary = $("draft-summary");
  summary.className = `summary ${!total ? "" : counts.ok === total ? "good" : counts.missing ? "bad" : "warn"}`;
  summary.textContent = !total ? "Your scenario will appear here."
    : counts.ok === total ? `All ${total} steps are ready to run.`
    : `${counts.ok} of ${total} steps ready` +
      (counts.missing ? ` · ${counts.missing} don't exist yet` : "") +
      (counts.other ? ` · ${counts.other} to fix` : "");
  renderGherkin();
  const missingTyped = state.draft.blocks.some((b, i) => b.kind === "text" && blockStatus(b, i) === "missing");
  $("stubs-btn").disabled = !missingTyped;
  $("stubs-btn").title = missingTyped ? "" : "Every typed step already exists";
  $("save-btn").disabled = !total;
}

function gherkinText() {
  const feature = state.draft.feature.trim() || "Untitled feature";
  const scenario = state.draft.scenario.trim() || "Untitled scenario";
  return [`Feature: ${feature}`, "", `  Scenario: ${scenario}`, ...draftLines().map((l) => `    ${l}`)].join("\n");
}

function renderGherkin() {
  const lines = draftLines();
  const pre = $("gherkin");
  if (!lines.length) {
    pre.replaceChildren(el("span", { class: "muted" }, "Feature: …\n\n  Scenario: …\n    Given …"));
    return;
  }
  const head = gherkinText().split("\n").slice(0, 3).join("\n") + "\n";
  pre.replaceChildren(head, ...lines.map((line, i) =>
    el("span", { class: `line status-${blockStatus(state.draft.blocks[i], i)}` }, `    ${line}\n`)));
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
  $("blocks").classList.remove("drop-over");
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

// --- duplicates ----------------------------------------------------------------

let dupesSeq = 0;
async function loadDupes() {
  const project = activeProject();
  if (!project) return;
  const seq = ++dupesSeq;
  const query = new URLSearchParams({ project: project.id, threshold: state.dupesThreshold });
  for (const fn of state.dupesKeep) query.append("keep", fn);
  try {
    const data = await api(`/api/dupes?${query}`);
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
  return n === 0 ? "not used in any feature file" : `used in ${n} feature line${n === 1 ? "" : "s"}`;
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
          ? `Merging rewrites ${pair.rewrites} feature line${pair.rewrites === 1 ? "" : "s"} and removes ${pair.drop.function}().`
          : `No feature file uses the second wording, so merging just removes ${pair.drop.function}().`),
        el("button", { type: "button", class: "primary", onclick: () => openMergeDialog(pair) }, "Preview merge…"))
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
  $("dupes-summary").textContent = n
    ? `${n} pair${n === 1 ? "" : "s"} of steps say the same thing in different words`
    : "";
  const list = data.pairs.map(dupeCard);
  if (data.skipped.length) {
    list.push(el("p", { class: "muted small" },
      `${data.skipped.length} more pair${data.skipped.length === 1 ? "" : "s"} share a step with one above. ` +
      "Merge those first and the rest will show up here."));
  }
  $("dupes-list").replaceChildren(...(list.length ? list : [
    el("div", { class: "all-clear" }, el("strong", {}, "No duplicates found."),
      el("span", { class: "muted" }, " Every step says something different. Slide the similarity lower to look harder.")),
  ]));
}

async function openMergeDialog(pair) {
  dialog.kind = "merge";
  const body = { project: state.active, threshold: state.dupesThreshold, pair: pair.id, keep: [...state.dupesKeep] };
  dialog.request = ["/api/merge", body];
  try {
    const preview = await api("/api/merge/preview", body);
    $("change-options").replaceChildren();
    showPreview(preview);
    $("change-back").hidden = true;
    $("change-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

// --- builder: save and stubs dialogs -----------------------------------------

const dialog = { kind: null, preview: null, request: null };

function slug(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
}

function radio(name, value, label, checked) {
  return el("label", { class: "radio" }, el("input", { type: "radio", name, value, checked }), label);
}

async function openChangeDialog(kind) {
  const project = activeProject();
  if (!project) return;
  if (kind === "save") {
    if (state.draft.blocks.some((b, i) => ["incomplete", "invalid"].includes(blockStatus(b, i)))) {
      return toast("Fill in the blanks first");
    }
  }
  let files;
  try {
    files = await api(`/api/files?project=${encodeURIComponent(project.id)}`);
  } catch (error) {
    return toast(error.message);
  }
  dialog.kind = kind;
  dialog.preview = null;
  const options = $("change-options");
  if (kind === "save") {
    $("change-title").textContent = "Save scenario";
    const existing = el("select", { name: "existing", "aria-label": "Feature file" },
      ...files.features.map((f) => el("option", { value: f }, f)));
    options.replaceChildren(
      el("div", { class: "radios" },
        radio("mode", "new", "Create a new feature file", true),
        radio("mode", "append", "Add to an existing file", false)),
      el("label", { class: "mode-new" }, "File name",
        el("span", { class: "with-suffix" },
          el("input", { name: "file", value: slug(state.draft.feature) || "new_feature", autocomplete: "off" }),
          el("span", { class: "suffix" }, ".feature"))),
      el("label", { class: "mode-append", hidden: true }, "Feature file", existing));
    if (!files.features.length) options.querySelector('input[value="append"]').disabled = true;
    const missing = state.draft.blocks.filter((b, i) => blockStatus(b, i) !== "ok").length;
    $("change-note").textContent = missing
      ? `${missing} step${missing === 1 ? " doesn't" : "s don't"} exist yet. You can still save; the scenario will fail until ${missing === 1 ? "it's" : "they're"} written.`
      : `It goes into ${project.features}.`;
    if (!state.draft.scenario.trim()) $("change-note").textContent = "Give the scenario a name first (above the steps).";
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
  $("change-submit").textContent = preview ? { save: "Save", stubs: "Add to file", merge: "Merge" }[dialog.kind] : "Preview";
  if (preview) {
    const files = preview.files || [preview];
    $("change-diff").replaceChildren(...files.flatMap((file) => [
      files.length > 1 ? el("span", { class: "file" }, `${file.file}\n`) : null,
      ...file.diff.slice(2).map((line) =>
        el("span", { class: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "" },
          `${line}\n`)),
    ].filter(Boolean)));
    $("change-title").textContent = dialog.kind === "merge"
      ? `Merge: change ${files.length} file${files.length === 1 ? "" : "s"}`
      : preview.creates ? `Create ${preview.file}` : `Change ${preview.file}`;
  }
}

function changeRequest() {
  const form = $("change-form").elements;
  const project = state.active;
  if (dialog.kind === "save") {
    const mode = form.mode.value;
    return ["/api/scenario", { project, mode, file: mode === "new" ? form.file.value : form.existing.value,
      feature: state.draft.feature, scenario: state.draft.scenario, lines: draftLines() }];
  }
  const only = state.draft.blocks.map((b, i) => (b.kind === "text" && blockStatus(b, i) === "missing" ? i : -1))
    .filter((i) => i >= 0);
  return ["/api/stubs", { project, file: form.file.value, lines: draftLines(), only }];
}

async function submitChange(event) {
  event.preventDefault();
  try {
    if (!dialog.preview) {
      const [base, body] = changeRequest();
      dialog.request = [base, body];
      showPreview(await api(`${base}/preview`, body));
      return;
    }
    const [base, body] = dialog.request;
    const result = await api(`${base}/save`, { ...body, hash: dialog.preview.hash });
    $("change-dialog").close();
    toast(dialog.kind === "save" ? `Saved to ${result.saved}`
      : dialog.kind === "merge" ? `Merged: updated ${result.saved.join(", ")}`
      : `Added steps to ${result.saved}`);
    if (dialog.kind !== "save") await loadSteps();
  } catch (error) {
    $("change-error").textContent = error.message;
    $("change-error").hidden = false;
  }
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

$("dupes-threshold").addEventListener("input", (e) => {
  state.dupesThreshold = Number(e.target.value);
  $("dupes-threshold-value").textContent = `${Math.round(state.dupesThreshold * 100)}%`;
  loadDupes();
});
$("palette-search").addEventListener("input", (e) => { state.paletteQuery = e.target.value; renderPalette(); });
$("draft-feature").addEventListener("input", (e) => { state.draft.feature = e.target.value; saveDraft(); renderGherkin(); });
$("draft-scenario").addEventListener("input", (e) => { state.draft.scenario = e.target.value; saveDraft(); renderGherkin(); });
$("typed-form").addEventListener("submit", (e) => {
  e.preventDefault();
  addTextBlock($("typed-input").value);
  $("typed-input").value = "";
});
$("build-clear").addEventListener("click", () => {
  if (state.draft.blocks.length && !confirm("Clear this scenario and start again?")) return;
  state.draft = emptyDraft();
  $("draft-feature").value = $("draft-scenario").value = "";
  changed();
});
$("copy-gherkin").addEventListener("click", () =>
  navigator.clipboard.writeText(gherkinText()).then(() => toast("Gherkin copied"), () => toast("Couldn't copy")));
for (const zone of [$("blocks"), $("drop-empty")]) {
  zone.addEventListener("dragover", onDragOver);
  zone.addEventListener("drop", onDrop);
  zone.addEventListener("dragleave", (e) => { if (!zone.contains(e.relatedTarget)) clearDropMarks(); });
}
document.addEventListener("dragend", clearDropMarks);

$("save-btn").addEventListener("click", () => openChangeDialog("save"));
$("stubs-btn").addEventListener("click", () => openChangeDialog("stubs"));
$("change-form").addEventListener("submit", submitChange);
$("change-options").addEventListener("change", syncModes);
$("change-back").addEventListener("click", () => showPreview(null));
$("change-cancel").addEventListener("click", () => $("change-dialog").close());

window.addEventListener("focus", () => { if (state.active) loadSteps(); }); // pick up edits made in the editor

state.tab = store("gc-tab") || "build";
loadProjects().catch((error) => toast(error.message));
