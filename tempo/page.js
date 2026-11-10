const state = {
  status: "open", project: storedProject(), seen: storedSeen(), opened: storedOpened(),
  current: null, shown: null, shownVersion: null, back: null, missing: null,
  // The question ("project/number") whose answer, follow-up or reopen is on its way.
  sending: null,
};

// The page shows one project at a time and remembers the last one per browser.
function storedProject() {
  try { return localStorage.getItem("project") || ""; } catch { return ""; }
}

function chooseProject(name) {
  state.project = name;
  try { localStorage.setItem("project", name); } catch { /* private window: just don't remember */ }
  // terminal.js, when the page loaded it: an open terminal follows the project.
  if (typeof followTerminal === "function") followTerminal();
}

// When the user last looked at each project: the project's activity time then, as the
// server gave it. Kept per browser; null until the first visit.
function storedSeen() {
  try { return JSON.parse(localStorage.getItem("seen")) || null; } catch { return null; }
}

function markSeen(activity) {
  // On a first visit nothing counts as new yet.
  if (!state.seen) state.seen = { ...activity };
  if (activity[state.project]) state.seen[state.project] = activity[state.project];
  try { localStorage.setItem("seen", JSON.stringify(state.seen)); } catch { /* just don't remember */ }
}

// The header's chips: every project with something for the user, meaning open questions
// or news since it was last viewed, plus the current project. 'fresh' puts a dot on it.
function chipProjects(projects, counts, activity, seen, current) {
  return projects.map((name) => ({
    name, open: counts[name] || 0, current: name === current,
    fresh: name !== current && Boolean(activity[name]) && !(seen?.[name] >= activity[name]),
  })).filter((chip) => chip.current || chip.open || chip.fresh);
}

// When the user last opened each open question: its activity time then, as the server
// gave it, by project and number. Kept per browser.
function storedOpened() {
  try { return JSON.parse(localStorage.getItem("opened")) || {}; } catch { return {}; }
}

// The record after the list is shown: the question on screen counts as opened now, and on
// a first visit to a project so does every open question there, so nothing is new yet.
// Questions no longer open, and projects that are gone, are dropped to keep it small.
function noteOpened(opened, projects, open, project, current) {
  const next = {};
  for (const name of projects) if (opened[name]) next[name] = {};
  const first = project && !next[project];
  if (first) next[project] = {};
  for (const q of open) {
    const now = q.project === project && (first || current?.number === q.number);
    const at = now ? q.activity : opened[q.project]?.[q.number];
    if (at && next[q.project]) next[q.project][q.number] = at;
  }
  return next;
}

// A dot on an open question Claude changed (asked, reworded, or added context to) since
// the user last opened it. A question waiting on Claude shows that instead.
function questionFresh(question, opened) {
  const at = opened[question.project]?.[question.number];
  return question.status === "open" && !question.waiting && Boolean(opened[question.project])
    && !(at >= question.activity);
}

const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

async function api(path, body) {
  const response = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : {});
  const data = await response.json();
  // The status tells a reply from the server apart from no reply at all.
  if (!response.ok) throw Object.assign(new Error(data.error || "Something went wrong"), { status: response.status });
  return data;
}

// The server this page first talked to. Its code and the page's code match; after a
// restart (often an update) the data may change shape, so the page reloads itself.
let firstServer = null;

function serverChanged(first, now) {
  return Boolean(first && now && (first.pid !== now.pid || first.started !== now.started));
}

async function checkServer() {
  try {
    const now = await api("/health");
    if (!firstServer) firstServer = now;
    else if (serverChanged(firstServer, now)) {
      keepDraft();
      location.reload();
    }
  } catch { /* restarting or down: try again on the next tick */ }
}

// What the user is typing for the question on screen, so a reload doesn't lose it. Kept
// for this tab only; loadDetail takes it back when it draws the same question.
function typedDraft() {
  return { text: $("#note").value, choice: document.querySelector("input[name=choice]:checked")?.value,
           version: state.shownVersion };
}

// A draft to draw a question with. The note is always kept, but a pick only on the wording
// it was made on: after a reword the same letter may name another option.
function keptDraft(draft, question) {
  const reworded = draft.version && draft.version !== question.version;
  return { ...draft, choice: reworded ? null : draft.choice, version: question.version };
}

function keepDraft() {
  if (!state.shown || !$("#note")) return;
  try { sessionStorage.setItem(`draft:${state.shown}`, JSON.stringify(typedDraft())); } catch { /* lost on reload */ }
}

function takeDraft(id) {
  try {
    const draft = JSON.parse(sessionStorage.getItem(`draft:${id}`));
    sessionStorage.removeItem(`draft:${id}`);
    return draft;
  } catch { return null; }
}

// A broken address (a bad %-escape such as #/%E0/1) counts as no question, so it can't
// make every refresh fail.
function route() {
  const match = location.hash.match(/^#\/(.+)\/(\d+)$/);
  if (!match) return null;
  try {
    return { project: decodeURIComponent(match[1]), number: Number(match[2]) };
  } catch { return null; }
}

// How long a question has been open, in the largest whole unit: "open 3 days".
function openFor(createdAt, now = Date.now()) {
  const minutes = Math.max(1, Math.floor((now - new Date(createdAt).getTime()) / 60000));
  for (const [size, unit] of [[24 * 60, "day"], [60, "hour"], [1, "minute"]]) {
    if (minutes >= size) {
      const amount = Math.floor(minutes / size);
      return `open ${amount} ${unit}${amount === 1 ? "" : "s"}`;
    }
  }
}

// The plain facts of an open question: quick to answer (a recommended option, at most
// three options, never reworded) and how long it has been open.
function facts(question) {
  if (question.status !== "open") return [];
  return [question.quick && el("span", { class: "badge quick" }, "quick"),
    el("span", { class: "age" }, openFor(question.created_at))];
}

function startHere(question) {
  return question.start_here && el("span", { class: "badge start" }, "Start here");
}

// After "Ask for more", until Claude replies. The list gets it from the server; a full
// question works it out from its thread.
function waitingOnClaude(question) {
  if (question.waiting !== undefined) return question.waiting;
  const last = question.entries[question.entries.length - 1];
  if (last?.kind !== "followup") return null;
  return last.delivered ? "seen" : "sent";
}

// The header says it in words; a list row shows only the moving dots, which catch the eye
// without crowding the title.
function waitBadge(question, compact = false) {
  if (!waitingOnClaude(question)) return null;
  const dots = (attrs) => el("span", { class: "dots", ...attrs }, el("i"), el("i"), el("i"));
  if (compact) return dots({ title: "Waiting on Claude", role: "img", "aria-label": "Waiting on Claude" });
  return el("span", { class: "badge wait working" }, "Waiting on Claude", dots({ "aria-hidden": "true" }));
}

async function loadList() {
  const data = await api(`/api/questions?${new URLSearchParams({ status: state.status })}`);
  const open = state.status === "open" ? data : await api("/api/questions?status=open");
  const current = route();
  if (current) chooseProject(current.project);
  if (!data.projects.includes(state.project)) {
    // Start with a project that has open questions, else the first one.
    const waiting = open.questions[0];
    chooseProject(waiting ? waiting.project : (data.projects[0] || ""));
  }
  const counts = {};
  for (const q of open.questions) counts[q.project] = (counts[q.project] || 0) + 1;
  markSeen(data.activity || {});
  renderProjectBar(data.projects, counts, data.activity || {});
  state.opened = noteOpened(state.opened, data.projects, open.questions, state.project, current);
  try { localStorage.setItem("opened", JSON.stringify(state.opened)); } catch { /* just don't remember */ }
  const words = $("#search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (q) => words.every((word) =>
    `${q.label} ${q.title} ${q.context} ${q.goal || ""}`.toLowerCase().includes(word));
  const questions = data.questions.filter((q) => q.project === state.project && matches(q));
  // Built aside, then swapped in only if it changed (see drawList).
  const list = el("div");
  const shown = { questions, counts, projects: data.projects };
  if (!questions.length) {
    // The empty screen beside the list says the same, so wide screens hide this line;
    // a search with no match is only explained here.
    list.append(el("p", { class: words.length ? "empty" : "empty repeat" }, data.projects.length
      ? (words.length ? "No questions match your search." : `No ${state.status} questions in ${state.project}.`)
      : "No questions yet."));
    drawList($("#list"), list);
    return shown;
  }
  // Open questions come grouped by goal from the server; a heading starts each group,
  // once any question has a goal. The ones without a goal come last, under "Other".
  const grouped = state.status === "open" && questions.some((q) => q.goal);
  let group;
  for (const question of questions) {
    if (grouped && (question.goal || null) !== group) {
      group = question.goal || null;
      list.append(el("h3", { class: "goal" }, group || "Other"));
    }
    const active = current && current.project === question.project && current.number === question.number;
    list.append(el("a", { href: `#/${encodeURIComponent(question.project)}/${question.number}`,
                          class: [question.start_here && "start", active && "active"].filter(Boolean).join(" "),
                          "aria-current": active && "page" },
      el("span", { class: "label" }, question.label, newDot(questionFresh(question, state.opened)),
        startHere(question), waitBadge(question, true)),
      el("span", { class: "title" }, question.title),
      question.start_here && el("span", { class: "reason" }, question.start_here),
      question.status === "open" && el("span", { class: "facts" }, facts(question))));
  }
  drawList($("#list"), list);
  return shown;
}

// The timed refresh redraws the list only when it changed, so a click isn't swallowed,
// and keeps the keyboard on the same question when it does.
function drawList(list, next) {
  if (next.innerHTML === list.innerHTML) return;
  const focused = list.contains(document.activeElement) && document.activeElement.getAttribute("href");
  list.replaceChildren(...next.childNodes);
  if (focused) [...list.querySelectorAll("a")].find((link) => link.getAttribute("href") === focused)?.focus();
}

function setStatus(status) {
  state.status = status;
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.status === status));
  });
}

// Settings has its own address, #/settings, and replaces the list and the question while
// open. It is drawn when it opens and after each save, never by the timed refresh, so
// it doesn't wipe what the user is typing.
function settingsOpen() {
  return location.hash === "#/settings";
}

function showSettings(open) {
  $("main").hidden = open;
  $("#settings").hidden = !open;
  $("#gear").setAttribute("aria-pressed", String(open));
}

async function openSettings() {
  showSettings(true);
  editing = null;
  renderSettings(await api("/api/settings"));
}

function closeSettings() {
  history.replaceState(null, "", state.back || location.pathname);
  state.back = null;
  state.current = null;
  refresh(true);
}

// Theme, font, text size and width belong to this browser, not the database: the head script
// applies them on load. The default is stored as no choice at all.
const display = [
  { key: "theme", label: "Theme", hint: "System follows your computer's light or dark mode.", standard: "system",
    choices: [["system", "System"], ["light", "Light"], ["dark", "Dark"]] },
  { key: "font", label: "Font", standard: "sans",
    choices: [["sans", "System sans"], ["serif", "Serif"], ["mono", "Monospace"]] },
  { key: "size", label: "Text size", standard: "medium",
    choices: [["small", "Small"], ["medium", "Medium"], ["large", "Large"]] },
  { key: "width", label: "Width", hint: "How many characters fit on a line of a question.", standard: "medium",
    choices: [["narrow", "Narrow · 68"], ["medium", "Medium · 80"], ["wide", "Wide · 100"]] },
];

function setDisplay(key, value, isDefault) {
  if (isDefault) delete document.documentElement.dataset[key];
  else document.documentElement.dataset[key] = value;
  if (key === "theme" && typeof rethemeTerminals === "function") rethemeTerminals();
  try {
    if (isDefault) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* private window: applies until the page reloads */ }
}

function renderAppearance() {
  return el("section", { class: "card" },
    el("h3", {}, "Appearance"),
    el("p", {}, "Saved in this browser only."),
    display.map(({ key, label, hint, standard, choices }) => {
      const current = document.documentElement.dataset[key] || standard;
      return el("div", { class: "row" },
        el("div", {}, el("strong", {}, label), hint && el("span", { class: "meta" }, hint)),
        el("div", { class: "segmented", role: "group", "aria-label": label }, choices.map(([value, name]) =>
          el("button", { "aria-pressed": String(value === current), onclick: (event) => {
            setDisplay(key, value, value === standard);
            event.currentTarget.parentNode.querySelectorAll("button").forEach((button) => {
              button.setAttribute("aria-pressed", String(button === event.currentTarget));
            });
          } }, name))));
    }));
}

// Whether each Claude session starts this page, kept in the database like `tempo config page`.
// With it off, Claude points to `tempo show` and `tempo answer` in the terminal instead.
function renderSessions(data) {
  const error = el("p", { class: "error", hidden: true });
  const choose = (on) => api("/api/settings/page", { on }).then(renderSettings, (problem) => {
    error.textContent = problem.message;
    error.hidden = false;
  });
  return el("section", { class: "card" },
    el("h3", {}, "Sessions"),
    el("p", {}, "Saved in the database, the same as ", el("code", {}, "tempo config page"), "."),
    el("div", { class: "row" },
      el("div", {}, el("strong", {}, "Start the page with each session"),
        el("span", { class: "meta" }, "Off: Claude points you to the terminal; ", el("code", {}, "tempo serve"),
          " still starts the page.")),
      el("div", { class: "segmented", role: "group", "aria-label": "Start the page with each session" },
        [[true, "On"], [false, "Off"]].map(([on, name]) =>
          el("button", { "aria-pressed": String(on === data.start_page), onclick: () => choose(on) }, name))),
      error));
}

// The terminal under the question, kept in the database like `tempo config terminal`. Off by
// default: with it on, the page can run any command as the user. Its font and size apply to
// open terminals at once. Off while terminals run first says what that ends, with buttons to
// go ahead or cancel, as Merge does: the page never opens a browser dialog.
function renderTerminal(data) {
  const error = el("p", { class: "error", hidden: true });
  const save = (body) => api("/api/settings/terminal", body);
  const failed = (problem) => {
    error.textContent = problem.message;
    error.hidden = false;
  };
  const turn = (on) => save({ on })
    .then((result) => ({ ...result, terminalNotice: on ? null : terminalOffNotice(result.ended) }))
    .then(renderSettings, failed);
  const confirm = el("div", { class: "confirm", role: "group", hidden: true, onkeydown: (event) => {
    if (event.key === "Escape") close();
  } });
  const close = () => {
    confirm.hidden = true;
    confirm.replaceChildren();
    confirm.parentNode.querySelector("[aria-pressed=false]")?.focus();
  };
  const choose = async (on) => {
    if (on || !data.terminal || typeof terminalRunning !== "function") return turn(on);
    let running;
    try {
      running = await terminalRunning();
    } catch (problem) {
      return failed(problem);
    }
    if (!running) return turn(false);
    const text = terminalOffQuestion(running);
    const yes = el("button", { class: "primary", onclick: () => {
      yes.disabled = true;
      turn(false).finally(() => { yes.disabled = false; });
    } }, "Turn off");
    confirm.replaceChildren(el("p", {}, text),
      el("div", { class: "actions" }, yes, el("button", { onclick: close }, "Cancel")));
    // Read out when the keyboard lands on the first button.
    confirm.setAttribute("aria-label", text);
    confirm.hidden = false;
    yes.focus();
  };
  const font = el("input", { value: data.terminal_font, placeholder: "System monospace", "aria-label": "Terminal font" });
  const size = el("input", {
    type: "number", min: 8, max: 32, value: data.terminal_size, "aria-label": "Terminal font size",
  });
  return el("section", { class: "card" },
    el("h3", {}, "Terminal"),
    el("p", {}, "Saved in the database, the same as ", el("code", {}, "tempo config terminal"), "."),
    data.terminalNotice && el("p", { class: "meta", role: "status" }, data.terminalNotice),
    el("div", { class: "row" },
      el("div", {}, el("strong", {}, "Terminal in the project's folder"),
        el("span", { class: "meta" }, "On: a Terminal button opens a shell there. The page can then run any command as you.")),
      el("div", { class: "segmented", role: "group", "aria-label": "Terminal in the project's folder" },
        [[true, "On"], [false, "Off"]].map(([on, name]) =>
          el("button", { "aria-pressed": String(on === data.terminal), onclick: () => choose(on) }, name))),
      error, confirm),
    el("div", { class: "row" },
      el("div", {}, el("strong", {}, "Terminal font"),
        el("span", { class: "meta" }, "Font names, such as \"JetBrains Mono\", monospace. Empty: the system's monospace font.")),
      saveForm(font, () => (font.value.trim() !== data.terminal_font ? font.value.trim() : null),
        (value) => save({ font: value }))),
    el("div", { class: "row" },
      el("div", {}, el("strong", {}, "Font size"), el("span", { class: "meta" }, "In pixels, from 8 to 32.")),
      saveForm(size, () => (size.value !== "" && Number(size.value) !== data.terminal_size ? Number(size.value) : null),
        (value) => save({ size: value }))));
}

// What turning the terminal off ends, shown before it happens.
function terminalOffQuestion(running) {
  return `Turning the terminal off ends ${count(running, "running terminal")}; anything running in `
    + `${running === 1 ? "it" : "them"} stops.`;
}

// Says how many terminals turning it off ended; nothing when none ran.
function terminalOffNotice(ended) {
  return ended ? `Terminal is off. Ended ${count(ended, "terminal")}.` : null;
}

// A small Settings form that saves one value, and the line for its errors. read() returns
// the value to save, or null while nothing changed; the button is enabled only for a change.
function saveForm(fields, read, save, label = "Save") {
  const error = el("p", { class: "error", hidden: true });
  const button = el("button", { type: "submit", disabled: true }, label);
  const form = el("form", {
    oninput: () => { button.disabled = read() === null; },
    onchange: () => { button.disabled = read() === null; },
    onsubmit: (event) => {
      event.preventDefault();
      button.disabled = true;
      save(read()).then(renderSettings, (problem) => {
        error.textContent = problem.message;
        error.hidden = false;
        button.disabled = false;
      });
    },
  }, fields, button);
  return [form, error];
}

function count(number, word) {
  return `${number} ${word}${number === 1 ? "" : "s"}`;
}

// The project whose Edit panel is open in Settings, by name, so a redraw keeps it open;
// only one opens at a time. editors shows or hides each drawn row's panel.
let editing = null;
const editors = new Map();

function renderProjects(data) {
  editors.clear();
  return el("section", { class: "card" },
    el("h3", {}, "Projects"),
    el("p", {}, "Rename a project, or merge one into another, such as a renamed folder's old project into its new one."),
    data.notice && el("p", { class: "meta", role: "status" }, data.notice),
    data.projects.length ? data.projects.map((project, index) => projectRow(project, data.projects, index))
      : el("p", { class: "empty" }, "No projects yet. One appears when Claude asks its first question."));
}

// A project's name, question count and folders, with an Edit button that opens Rename and
// Merge into… below them. Esc closes it; closing drops what was typed or picked there.
function projectRow(project, projects, index) {
  const input = el("input", { value: project.name, "aria-label": `New name for ${project.name}` });
  const edit = el("button", { type: "button", class: "edit", "aria-label": editLabel(project.name),
    "aria-expanded": String(editing === project.name), "aria-controls": `editor-${index}`,
    onclick: () => openEditor(editing === project.name ? null : project.name) }, "Edit");
  const editor = el("div", { class: "editor", id: `editor-${index}`, hidden: editing !== project.name },
    saveForm(input,
      () => (input.value.trim() && input.value.trim() !== project.name ? input.value.trim() : null),
      (name) => api(`/api/projects/${encodeURIComponent(project.name)}/rename`, { name }).then((result) => {
        renamed(project.name, name);
        editing = null;
        return { ...result, notice: joinNotice(project.name, name, result.moved || []), focus: editLabel(name) };
      }), "Rename"),
    mergeForm(project, projects));
  editors.set(project.name, (open) => {
    if (!open && !editor.hidden) {
      for (const form of editor.querySelectorAll("form")) {
        form.reset();
        form.dispatchEvent(new Event("change"));
      }
      for (const error of editor.querySelectorAll(".error")) error.hidden = true;
    }
    editor.hidden = !open;
    edit.setAttribute("aria-expanded", String(open));
  });
  return el("div", { class: "row project", onkeydown: (event) => {
    if (event.key !== "Escape" || editor.hidden) return;
    openEditor(null);
    edit.focus();
  } },
  el("div", {}, el("strong", {}, project.name),
    el("span", { class: "meta" }, count(project.questions, "question")),
    renderFolders(project)),
  edit, editor);
}

function editLabel(name) {
  return `Edit ${name}`;
}

// Opens one project's Edit panel and closes the others; null closes them all. The
// keyboard moves to the new name, selected, so typing replaces it.
function openEditor(name) {
  editing = name;
  for (const [project, show] of editors) show(project === name);
  const field = name && document.querySelector("#settings .editor:not([hidden]) input");
  if (field) {
    field.focus();
    field.select();
  }
}

// Merges a project into another one picked from a list. "Merge…" first shows what will
// happen, with buttons to go ahead or cancel: the page never opens a browser dialog, so
// it stays usable when a script drives it. Joining is a rename to the other's name.
function mergeForm(project, projects) {
  const others = projects.filter((other) => other.name !== project.name);
  const select = el("select", { "aria-label": `Merge ${project.name} into`, disabled: !others.length },
    el("option", { value: "" }, others.length ? "Merge into…" : "No other project to merge into"),
    others.map((other) => el("option", { value: other.name }, other.name)));
  const button = el("button", { type: "submit", disabled: true }, "Merge…");
  const error = el("p", { class: "error", hidden: true });
  const confirm = el("div", { class: "confirm", role: "group", hidden: true, onkeydown: (event) => {
    if (event.key !== "Escape") return;
    // A second Esc closes the Edit panel around it.
    event.stopPropagation();
    close();
    select.focus();
  } });
  const close = () => {
    confirm.hidden = true;
    confirm.replaceChildren();
  };
  const merge = (target, yes) => {
    yes.disabled = true;
    api(`/api/projects/${encodeURIComponent(project.name)}/rename`, { name: target.name }).then((result) => {
      renamed(project.name, target.name);
      editing = null;
      return { ...result, notice: mergeNotice(project.name, target.name, project.questions, result.moved || []),
        focus: editLabel(target.name) };
    }).then(renderSettings, (problem) => {
      error.textContent = problem.message;
      error.hidden = false;
      yes.disabled = false;
    });
  };
  const form = el("form", {
    onchange: () => {
      button.disabled = !select.value;
      close();
    },
    onsubmit: (event) => {
      event.preventDefault();
      const target = others.find((other) => other.name === select.value);
      if (!target) return;
      const text = mergeQuestion(project, target);
      const yes = el("button", { class: "primary", onclick: () => merge(target, yes) }, `Merge into ${target.name}`);
      const no = el("button", { onclick: () => { close(); select.focus(); } }, "Cancel");
      confirm.replaceChildren(el("p", {}, text), el("div", { class: "actions" }, yes, no));
      // Read out when the keyboard lands on the first button.
      confirm.setAttribute("aria-label", text);
      confirm.hidden = false;
      yes.focus();
    },
  }, select, button);
  return [form, confirm, error];
}

// What merging a project into another does, shown before it happens.
function mergeQuestion(project, target) {
  const what = project.questions ? `Its ${count(project.questions, "question")} and folders` : "Its folders";
  const renumber = project.questions && target.questions
    ? ` They get numbers after ${target.name}'s; each keeps a note of its old one.` : "";
  return `Merge ${project.name} into ${target.name}? ${what} move to ${target.name}, `
    + `and ${project.name} goes away.${renumber}`;
}

// Says where a merged project's questions went: their new numbers when they changed,
// otherwise how many moved.
function mergeNotice(from, to, questions, moved) {
  if (moved.length) return joinNotice(from, to, moved);
  if (!questions) return `Merged ${from} into ${to}.`;
  return `Moved ${count(questions, "question")} from ${from} to ${to}, with the same numbers.`;
}

// Says where a joined project's questions went, e.g. "Moved 5 questions from tempo to
// Tempo: now Q28–Q32." Nothing when no numbers changed.
function joinNotice(from, to, moved) {
  if (!moved.length) return null;
  const first = moved[0].to, last = moved[moved.length - 1].to;
  const numbers = first === last ? `Q${first}` : `Q${first}–Q${last}`;
  return `Moved ${count(moved.length, "question")} from ${from} to ${to}: now ${numbers}.`;
}

// The folders a project covers, each with its full path, and "folder gone" for one that
// no longer exists.
function renderFolders(project) {
  if (!project.folders.length) return null;
  return el("ul", { class: "folders", "aria-label": "Folders" }, project.folders.map((folder) =>
    el("li", {}, folder.folder, " ", el("span", { class: "path", title: folder.path }, pathParts(folder.shown)),
      folder.gone && el("span", { class: "gone" }, "folder gone"))));
}

// A path that wraps only after a /, never inside a folder's name: a <wbr> follows each /.
function pathParts(path) {
  return (path || "").split(/(?<=\/)/).flatMap((part, index) => (index ? [el("wbr"), part] : [part]));
}

// Keep the page on a renamed project, and the way back pointing at the same question.
function renamed(from, to) {
  if (state.project === from) chooseProject(to);
  const back = (state.back || "").match(/^#\/(.+)\/(\d+)$/);
  if (back && decodeURIComponent(back[1]) === from) state.back = `#/${encodeURIComponent(to)}/${back[2]}`;
}

// Names being typed, and projects picked to merge into, are kept, by field, so a redraw
// from elsewhere (such as the timed refresh) doesn't wipe them. A saved rename's field
// has a new label, so it starts fresh.
function renderSettings(data) {
  const fields = () => document.querySelectorAll("#settings input, #settings select");
  const typed = new Map([...fields()]
    .filter((field) => (field.tagName === "SELECT" ? field.value !== "" : field.value !== field.defaultValue))
    .map((field) => [field.getAttribute("aria-label"), field.value]));
  const active = document.activeElement;
  const focused = $("#settings").contains(active) && controlKey(active);
  drawSettings(data);
  if (typeof applyTerminalSettings === "function") applyTerminalSettings(data);
  for (const field of fields()) {
    if (!typed.has(field.getAttribute("aria-label"))) continue;
    field.value = typed.get(field.getAttribute("aria-label"));
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }
  // The redraw replaces every control, so the keyboard goes back to the one it was on.
  // After a save, it goes to the button the save names, such as the renamed project's Edit.
  const controls = [...document.querySelectorAll("#settings button, #settings input, #settings select")];
  if (data.focus) controls.find((node) => node.getAttribute("aria-label") === data.focus)?.focus();
  else if (focused) controls.find((node) => controlKey(node) === focused)?.focus();
}

// Names a Settings control so its copy can be found after a redraw: a field by its label,
// a button by its text and the label of its group or of its form's field.
function controlKey(node) {
  const group = node.closest("[role=group]")?.getAttribute("aria-label")
    || node.closest("form")?.querySelector("input, select")?.getAttribute("aria-label") || "";
  return [node.tagName, node.getAttribute("aria-label") || node.textContent, group].join("\n");
}

function drawSettings(data) {
  $("#settings").replaceChildren(el("div", { class: "settings" },
    el("button", { class: "back", onclick: closeSettings }, "← Back to questions"),
    el("h2", {}, "Settings"),
    renderAppearance(),
    renderSessions(data),
    renderTerminal(data),
    renderProjects(data),
    // Placeholder: what belongs here is still to be decided.
    el("section", { class: "card" },
      el("h3", {}, "Question rules", el("span", { class: "badge muted" }, "coming soon")),
      el("p", {}, "Rules for how Claude asks and handles your questions will live here. Nothing to set yet."))));
}

// Draws the list and the question on the right. The question shown must belong to the
// selected project and tab: otherwise the first one in the list opens, or, when the list
// is empty, an empty screen. 'follow' means the address changed on purpose (a link or a
// click), so the tab switches to match the question instead of dropping it.
async function refresh(follow = false) {
  if (settingsOpen()) {
    if ($("#settings").hidden) await openSettings();
    // The header's project chips stay visible, so keep it filled and up to date
    // (for example after a rename here).
    await loadList();
    return;
  }
  showSettings(false);
  let current = route();
  if (follow) state.missing = null;
  if (current && follow) {
    try {
      const question = await api(`/api/questions/${encodeURIComponent(current.project)}/${current.number}`);
      if (question.status !== state.status) setStatus(question.status);
    } catch (problem) {
      // The server says it doesn't exist; a server that is restarting says nothing.
      if (problem.status) state.missing = { hash: location.hash, message: problem.message };
    }
  }
  const shown = await loadList();
  current = route();
  // A link to a missing question (say, one renumbered when two projects were joined) says
  // so, and stays until the user moves on, instead of opening another question.
  if (current && state.missing?.hash === location.hash) {
    state.current = null;
    state.shown = null;
    $("#detail").replaceChildren(el("div", { class: "empty-state" }, el("h2", {}, "Question not found"),
      el("p", {}, `${state.missing.message}. Pick a question from the list.`)));
    return;
  }
  const visible = (q) => current && q.project === current.project && q.number === current.number;
  if (current && !shown.questions.some(visible)) {
    if (await keptOnScreen(current)) return;
    history.replaceState(null, "", location.pathname);
    current = null;
  }
  if (!current && shown.questions.length) {
    const first = shown.questions[0];
    history.replaceState(null, "", `#/${encodeURIComponent(first.project)}/${first.number}`);
    state.current = null;
    await loadList();
    current = route();
  }
  if (!current) {
    state.current = null;
    state.shown = null;
    // Redrawn only when it changes, so the timed refresh keeps focus on its buttons.
    const key = JSON.stringify([state.status, state.project, shown.projects, shown.counts]);
    if ($("#detail").firstChild?.dataset?.key === key) return;
    const empty = renderEmpty(shown);
    empty.dataset.key = key;
    $("#detail").replaceChildren(empty);
    return;
  }
  await loadDetail(follow);
}

// The question on screen left the list while the user was answering it: answered or
// archived elsewhere (the terminal screen, the tempo command, another tab), or hidden by
// the search. The page stays on it, so the note being typed isn't lost, and says why it
// left. Without a draft, or once the user moves on, the page opens the next question.
async function keptOnScreen(current) {
  const id = `${current.project}/${current.number}`;
  if (!state.current || state.shown !== id || !$("#note")) return false;
  const typed = typedDraft();
  if (!typed.text.trim() && !typed.choice) return false;
  if ($("#detail .changed.left")) return true;
  let status = "gone";
  try {
    status = (await api(`/api/questions/${encodeURIComponent(current.project)}/${current.number}`)).status;
  } catch (problem) {
    // A server that is restarting says nothing: try again on the next tick.
    if (!problem.status) return true;
  }
  // Still open: only the search hides it, so it stays without a word. A second refresh
  // may have said it already while this one waited.
  if (status === "open" || $("#detail .changed.left")) return true;
  const what = { answered: "was answered elsewhere", archived: "was archived elsewhere" }[status]
    || "is no longer here";
  const moveOn = () => {
    state.current = null;
    state.shown = null;
    history.replaceState(null, "", location.pathname);
    refresh();
  };
  $("#detail article")?.prepend(el("div", { class: "changed left", role: "status" },
    el("p", {}, `Q${current.number} ${what}, so it is no longer open. Your note is still here to copy.`),
    el("button", { class: "link", onclick: moveOn }, "Move on")));
  return true;
}

// The Tempo mark is a fermata: the sign that holds a note until the conductor releases it.
function mark() {
  const holder = el("div");
  holder.innerHTML = document.querySelector(".brand svg").outerHTML;
  return holder.firstChild;
}

function renderEmpty(shown) {
  if (!shown.projects.length) {
    return el("div", { class: "empty-state" }, mark(), el("h2", {}, "No questions yet"),
      el("p", {}, "When Claude needs a decision from you, it shows up here."));
  }
  // The busiest few only; with many projects the rest stay in the header's project menu.
  const others = shown.projects.filter((name) => name !== state.project && shown.counts[name])
    .sort((a, b) => shown.counts[b] - shown.counts[a]);
  const SHOWN = 3;
  const more = others.length - SHOWN;
  const heading = state.status === "open"
    ? "Nothing is waiting on you"
    : `No ${state.status} questions`;
  return el("div", { class: "empty-state" }, mark(),
    el("h2", {}, heading),
    el("p", {}, state.status === "open"
      ? "Every question here is answered. New ones appear on their own."
      : `Questions move here once they are ${state.status}.`),
    state.status === "open" && el("button", {
      class: "link", onclick: () => { setStatus("answered"); state.current = null; refresh(); },
    }, "See answered questions →"),
    others.length > 0 && el("section", { class: "elsewhere" },
      el("h3", {}, "Still open elsewhere"),
      el("ul", {}, others.slice(0, SHOWN).map((name) => el("li", {}, el("button", {
        onclick: () => { setStatus("open"); chooseProject(name); state.current = null; refresh(); },
      }, el("span", { class: "name" }, name),
        el("span", { class: "count" }, `${shown.counts[name]} open`),
        el("span", { class: "chevron", "aria-hidden": "true" }, "›"))))),
      // The click must not reach the page, which closes the menu on outside clicks.
      more > 0 && el("button", { class: "link more", onclick: (event) => { event.stopPropagation(); openProjectMenu(); } },
        `${more} more ${more === 1 ? "project" : "projects"} in All projects ↑`)));
}

// Redrawn only when it changes, so the timed refresh keeps the menu and focus as they are.
function renderProjectBar(projects, counts, activity) {
  const bar = $("#projects");
  const chips = chipProjects(projects, counts, activity, state.seen, state.project);
  const key = JSON.stringify([chips, projects.map((name) => counts[name] || 0)]);
  bar.hidden = !projects.length;
  if (bar.dataset.key === key) return;
  bar.dataset.key = key;
  $("#chips").replaceChildren(...chips.map((chip) => el("button", {
    class: "chip", "aria-pressed": String(chip.current), onclick: () => switchProject(chip.name),
  }, chip.name, el("span", { class: "count" }, ` · ${chip.open}`),
  newDot(chip.fresh))));
  // Rebuilding the open menu keeps the keyboard on the same row.
  const focused = [...document.querySelectorAll("#project-menu button")].indexOf(document.activeElement);
  $("#project-menu").replaceChildren(...projects.map((name) => el("li", { role: "none" }, el("button", {
    role: "menuitem", tabindex: "-1", "aria-current": name === state.project ? "true" : false,
    onclick: () => switchProject(name),
  }, el("span", { class: "name" }, name), el("span", { class: "count" }, `${counts[name] || 0} open`)))));
  if (focused >= 0) document.querySelectorAll("#project-menu button")[focused]?.focus();
}

// The dot on a chip or a list row that marks something new.
function newDot(show) {
  return show && el("span", { class: "dot", title: "Something new" }, el("span", { class: "sr" }, ", something new"));
}

function switchProject(name) {
  closeProjectMenu(false);
  chooseProject(name);
  history.replaceState(null, "", location.pathname);
  state.current = null;
  refresh();
}

function openProjectMenu() {
  $("#project-menu").hidden = false;
  $("#all-projects").setAttribute("aria-expanded", "true");
  $("#project-menu button")?.focus();
}

function closeProjectMenu(refocus) {
  if ($("#project-menu").hidden) return;
  $("#project-menu").hidden = true;
  $("#all-projects").setAttribute("aria-expanded", "false");
  if (refocus) $("#all-projects").focus();
}

// Up and down (and Home, End) move through the menu's projects; Escape closes it.
function projectMenuKeys(event) {
  const items = [...document.querySelectorAll("#project-menu button")];
  const at = items.indexOf(document.activeElement);
  const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
  if (event.key === "Escape") closeProjectMenu(true);
  if (to === undefined || !items.length) return;
  event.preventDefault();
  items[(to + items.length) % items.length].focus();
}

async function loadDetail(force) {
  const current = route();
  const detail = $("#detail");
  if (!current) return;
  // A slow reply for a question the user has since left is dropped.
  const hash = location.hash;
  const moved = () => location.hash !== hash;
  let question;
  try {
    question = await api(`/api/questions/${encodeURIComponent(current.project)}/${current.number}`);
  } catch (error) {
    if (!moved()) detail.replaceChildren(el("p", { class: "error" }, error.message));
    return;
  }
  if (moved()) return;
  const key = JSON.stringify([question.project, question.number, question.version,
                              question.status, question.entries.length, question.goal, question.start_here]);
  if (!force && key === state.current) return;
  // Keep what the user is typing when the same question refreshes underneath them, or
  // what they had typed before the page reloaded.
  const id = `${question.project}/${question.number}`;
  const typed = state.shown === id && $("#note") ? typedDraft() : null;
  const draft = keptDraft(typed || takeDraft(id) || { text: "", choice: null }, question);
  // A redraw of the same question keeps the note box focused, with the cursor where it was.
  const old = $("#note");
  const cursor = state.shown === id && old && document.activeElement === old
    && [old.selectionStart, old.selectionEnd, old.selectionDirection];
  state.current = key;
  state.shown = id;
  state.shownVersion = question.version;
  detail.replaceChildren(renderQuestion(question, draft, typed && rewordNotice(typed, question)));
  if (cursor && $("#note")) {
    $("#note").focus();
    $("#note").setSelectionRange(...cursor);
  }
}

// What to tell the user when Claude rewords the question they are answering, or null when
// they hadn't started. Their pick is dropped (keptDraft), so the notice says so.
function rewordNotice(typed, question) {
  if (!typed.version || typed.version === question.version) return null;
  if (!typed.text.trim() && !typed.choice) return null;
  return [`Claude reworded this question while you were answering it (now version ${question.version}).`,
    typed.choice && "Your pick was cleared: read the options again.",
    typed.text.trim() && "Your note is kept."].filter(Boolean).join(" ");
}

function renderQuestion(question, draft, notice = null) {
  const reworded = question.version > 1 ? `, reworded (version ${question.version})` : "";
  const parts = [
    notice && el("div", { class: "changed", role: "status" }, el("p", {}, notice)),
    el("div", { class: "eyebrow" }, `${question.project} · ${question.label} · ${question.status}${reworded}`,
       waitBadge(question)),
    el("h2", {}, question.title),
    el("div", { class: "facts" },
      el("span", { class: "badge goal" }, question.goal ? `Goal: ${question.goal}` : "No goal"),
      facts(question)),
    question.start_here && el("p", { class: "start-here" }, startHere(question), question.start_here),
    el("div", { class: "context" }, rich(question.context)),
  ];
  const reopened = question.status === "open" && question.answer;
  if (question.answer) {
    const chosen = question.versions[question.answer.version - 1].options
      .find((option) => option.letter === question.answer.choice);
    const answeredVersion = question.answer.version !== question.version
      ? ` (on version ${question.answer.version})` : "";
    parts.push(el("div", { class: "answered" },
      el("strong", {}, `${reopened ? "Your previous answer" : "Your answer"}${answeredVersion}: `),
      chosen ? `(${chosen.letter}) ${chosen.text}` : "",
      question.answer.body ? (chosen ? " · " : "") + question.answer.body : ""));
  }
  const error = el("p", { class: "error", id: "action-error", hidden: true });
  // Buttons stay disabled while a request runs, so a double click can't send twice, even
  // when the timed refresh redraws the question meanwhile.
  const id = `${question.project}/${question.number}`;
  const busy = state.sending === id;
  const post = (path, body) => {
    state.sending = id;
    document.querySelectorAll("#detail .actions button").forEach((button) => { button.disabled = true; });
    return api(`/api/questions/${encodeURIComponent(question.project)}/${question.number}/${path}`, body)
      .finally(() => { state.sending = null; })
      .catch((problem) => {
        // The page may have been redrawn while the request ran, so the failure goes to
        // what is on screen now, naming the question if it is another one.
        document.querySelectorAll("#detail .actions button").forEach((button) => { button.disabled = false; });
        if ($("#send")) $("#send").disabled = !document.querySelector("input[name=choice]:checked");
        const line = $("#action-error") || error;
        const elsewhere = state.shown !== id;
        line.textContent = elsewhere ? `${question.label}: ${problem.message}` : problem.message;
        line.hidden = false;
        throw problem;
      });
  };
  const redraw = () => { state.current = null; state.shown = null; return Promise.all([loadDetail(true), loadList()]); };
  if (question.status === "answered") {
    // Answered questions are read-only; reopening is a deliberate step, so an old tab
    // can't add a second answer by accident. The question is open again, so the page
    // moves to the Open tab with it; otherwise the next refresh drops it.
    const reopen = () => { setStatus("open"); return redraw(); };
    parts.push(el("div", { class: "actions" },
      el("button", { disabled: busy, onclick: () => post("reopen", {}).then(reopen, () => {}) },
        "Reopen to answer again")), error);
  } else if (question.status === "open") {
    parts.push(el("div", { class: "options", role: "radiogroup", "aria-label": question.title },
      question.options.map((option) => el("label", {},
        el("input", { type: "radio", name: "choice", value: option.letter,
                      checked: option.letter === draft.choice }),
        el("span", { class: "letter" }, option.letter),
        el("span", { class: "text" }, option.text,
           option.letter === question.recommended && el("span", { class: "badge rec" }, "recommended"))))));
    const note = el("textarea", { id: "note", "aria-label": "Your note",
      placeholder: "A note with your answer, or what you need to know more about" });
    note.value = draft.text;
    parts.push(note, el("div", { class: "actions" },
      el("button", { class: "primary", id: "send", disabled: busy || !draft.choice, onclick: () => {
        const picked = document.querySelector("input[name=choice]:checked");
        // The version drawn, so the server refuses the answer if Claude has reworded the
        // question since: the same letter may name another option now. A refused answer
        // shows at once what changed, keeping the note.
        post("answer", { choice: picked ? picked.value : null, body: note.value, version: question.version })
          .then(() => goToNextOpen(question), () => refresh());
      } }, reopened ? "Send new answer" : "Send answer"),
      el("button", { disabled: busy, onclick: () => post("followup", { body: note.value }).then(redraw, () => {}) },
        "Ask for more context")),
      el("p", { class: "hint" }, "Pick an option to answer. To send only a note, use Ask for more context: the question stays open."),
      error);
  }
  parts.push(renderThread(question));
  return el("article", {}, parts);
}

async function goToNextOpen(answered) {
  // After an answer, move on to the next open question in the same project.
  state.current = null;
  state.shown = null;
  const data = await api(`/api/questions?status=open&project=${encodeURIComponent(answered.project)}`);
  const next = data.questions.find((q) => q.number !== answered.number);
  if (next) {
    location.hash = `#/${encodeURIComponent(next.project)}/${next.number}`;
  } else {
    history.replaceState(null, "", location.pathname);
    await refresh();
  }
}

// Context and thread text may use a little Markdown: blank lines between paragraphs,
// "- " or "1. " lists, **bold**, `code` and bare links. Built as DOM nodes, never HTML,
// so text from the database can't inject markup.
function rich(text) {
  const holder = el("div", { class: "rich" });
  const kinds = [["ul", /^\s*[-*]\s+/], ["ol", /^\s*\d+[.)]\s+/]];
  for (const block of String(text).split(/\n\s*\n/)) {
    // Within a paragraph, a run of list lines becomes a list; other lines stay text.
    let open = null;
    for (const line of block.split("\n").filter((candidate) => candidate.trim())) {
      const kind = kinds.find(([, pattern]) => pattern.test(line));
      if (kind) {
        if (open?.tagName.toLowerCase() !== kind[0]) {
          open = el(kind[0]);
          holder.append(open);
        }
        open.append(el("li", {}, inline(line.replace(kind[1], ""))));
      } else {
        if (open?.tagName !== "P") {
          open = el("p");
          holder.append(open);
        } else {
          open.append(el("br"));
        }
        open.append(...inline(line));
      }
    }
  }
  return holder;
}

function inline(text) {
  const parts = [];
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parts.push(text.slice(last, match.index));
    if (match[1]) parts.push(el("strong", {}, inline(match[1])));
    else if (match[2]) parts.push(el("code", {}, match[2]));
    else parts.push(el("a", { href: match[3], target: "_blank", rel: "noopener noreferrer" }, match[3]));
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));
  return parts.filter((part) => part !== "");
}

function renderThread(question) {
  const describe = {
    context: "Claude added context",
    reword: "Claude reworded the question",
    followup: "You asked for more",
    answer: "You answered",
  };
  const entries = question.entries.map((entry) => {
    const option = entry.choice && question.versions[entry.version - 1].options
      .find((candidate) => candidate.letter === entry.choice);
    const body = [option && `(${option.letter}) ${option.text}`, entry.body].filter(Boolean).join("\n");
    return el("div", { class: `entry ${entry.author}` },
      el("div", { class: "meta" }, `${describe[entry.kind]} · version ${entry.version} · ${new Date(entry.created_at).toLocaleString()}`),
      body && el("div", { class: "body" }, rich(body)));
  });
  return el("section", { class: "thread" }, el("h3", {}, "Thread"),
    el("div", { class: "timeline" },
      el("div", { class: "asked meta" }, `Asked ${new Date(question.versions[0].created_at).toLocaleString()}`),
      entries));
}

// Wires up the page. Node loads this file only to test rich(), inline() and openFor(), so it
// exports them instead of starting.
function start() {
  for (const button of document.querySelectorAll(".tabs button")) {
    button.addEventListener("click", () => {
      setStatus(button.dataset.status);
      state.current = null;
      if (settingsOpen()) history.replaceState(null, "", location.pathname);
      refresh();
    });
  }
  $("#gear").addEventListener("click", () => {
    if (settingsOpen()) return closeSettings();
    state.back = location.hash;
    location.hash = "#/settings";
  });
  window.addEventListener("hashchange", () => refresh(true));
  // Answering needs a picked option; a note alone goes through "Ask for more context".
  $("#detail").addEventListener("change", (event) => {
    if (event.target.name === "choice" && $("#send") && !state.sending) $("#send").disabled = false;
  });
  $("#search").addEventListener("input", () => refresh());
  $("#all-projects").addEventListener("click", () => {
    if ($("#project-menu").hidden) openProjectMenu();
    else closeProjectMenu(true);
  });
  $(".project-menu").addEventListener("keydown", projectMenuKeys);
  // A click or a Tab anywhere else closes the menu.
  for (const type of ["click", "focusin"]) {
    document.addEventListener(type, (event) => {
      if (!event.target.closest?.(".project-menu")) closeProjectMenu(false);
    });
  }

  refresh(true);
  checkServer();
  setInterval(() => { refresh(false); checkServer(); }, 4000);
}

if (typeof module === "object") module.exports = {
  rich, inline, openFor, joinNotice, mergeNotice, mergeQuestion, mergeForm, serverChanged, renderFolders, chipProjects,
  pathParts, projectRow, noteOpened, questionFresh, keptDraft, renderQuestion, renderSessions, renderTerminal, terminalOffQuestion, terminalOffNotice, rewordNotice, state,
};
else start();
