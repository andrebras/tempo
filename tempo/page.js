const state = {
  status: "open", project: storedProject(), seen: storedSeen(), opened: storedOpened(),
  current: null, shown: null, back: null, missing: null,
};

// The page shows one project at a time and remembers the last one per browser.
function storedProject() {
  try { return localStorage.getItem("project") || ""; } catch { return ""; }
}

function chooseProject(name) {
  state.project = name;
  try { localStorage.setItem("project", name); } catch { /* private window: just don't remember */ }
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
  return { text: $("#note").value, choice: document.querySelector("input[name=choice]:checked")?.value };
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

function route() {
  const match = location.hash.match(/^#\/(.+)\/(\d+)$/);
  return match ? { project: decodeURIComponent(match[1]), number: Number(match[2]) } : null;
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
  renderRenames(data.renames || []);
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
                          class: [question.start_here && "start", active && "active"].filter(Boolean).join(" ") },
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

// A banner above every screen for each new folder that may be a known one renamed or
// moved. It stays until the user links the folder or keeps it apart. Redrawn only when
// it changes, so the timed refresh doesn't swallow a click.
function renderRenames(renames) {
  const notice = $("#notice");
  const key = JSON.stringify(renames);
  if (notice.dataset.key === key) return;
  notice.dataset.key = key;
  notice.hidden = !renames.length;
  notice.replaceChildren(...renames.map(renameBanner));
}

// One new folder's banner: a "Link to <project>" button for each known folder that no
// longer exists, named with that folder's path, and a button to keep it apart.
function renameBanner(rename) {
  const error = el("span", { class: "error", hidden: true });
  const act = (path, body) => api(path, body).then((settings) => {
    if (body.project && state.project === rename.project) chooseProject(body.project);
    if (settingsOpen()) renderSettings(settings);
    return refresh();
  }, (problem) => { error.textContent = problem.message; error.hidden = false; });
  const keep = el("button", { onclick: () => act("/api/folders/keep", { path: rename.path }) },
    "Keep as its own project");
  const several = rename.candidates.length > 1;
  const links = rename.candidates.map((old) => el("button", {
    class: "primary",
    onclick: () => act("/api/folders/link", { path: rename.path, project: old.project }),
  }, several ? `Link to ${old.project} (${old.shown})` : `Link to ${old.project}`));
  const where = [el("code", {}, rename.folder), ` (${rename.shown}) is a new folder`];
  const text = several
    ? [". Several known folders no longer exist, so it may be one of them renamed or moved. "
      + "Link it to the one it was, or keep it apart."]
    : [", and ", el("code", {}, rename.candidates[0].shown),
      `, filed under ${rename.candidates[0].project}, no longer exists. `
      + "Is it the same folder, renamed or moved?"];
  return el("div", { class: "rename", role: "status" },
    el("p", {}, ...where, ...text), el("div", { class: "actions" }, ...links, keep, error));
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

// A Settings row: what it is on the left, a small form on the right. read() returns the
// value to save, or null while nothing changed; the button is enabled only for a change.
function saveRow(about, fields, read, save, label = "Save") {
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
  return el("div", { class: "row" }, about, form, error);
}

function count(number, word) {
  return `${number} ${word}${number === 1 ? "" : "s"}`;
}

function renderProjects(data) {
  return el("section", { class: "card" },
    el("h3", {}, "Projects"),
    el("p", {}, "A project's name is what you see everywhere. Renaming it takes its questions and the "
      + "git folders it covers along. To join two projects, rename one to the other's name. If both have questions, "
      + "the renamed one's questions move after the other's and get new numbers; each keeps a note of its old one."),
    data.notice && el("p", { class: "meta", role: "status" }, data.notice),
    data.projects.length ? data.projects.map((project) => {
      const input = el("input", { value: project.name, "aria-label": `New name for ${project.name}` });
      return saveRow(
        el("div", {}, el("strong", {}, project.name),
          el("span", { class: "meta" }, count(project.questions, "question")),
          renderFolders(project)),
        input,
        () => (input.value.trim() && input.value.trim() !== project.name ? input.value.trim() : null),
        (name) => api(`/api/projects/${encodeURIComponent(project.name)}/rename`, { name }).then((result) => {
          renamed(project.name, name);
          return { ...result, notice: joinNotice(project.name, name, result.moved || []) };
        }), "Rename");
    }) : el("p", { class: "empty" }, "No projects yet. One appears when Claude asks its first question."));
}

// Says where a joined project's questions went, e.g. "Moved 5 questions from tempo to
// Tempo: now Q28–Q32." Nothing when no numbers changed.
function joinNotice(from, to, moved) {
  if (!moved.length) return null;
  const first = moved[0].to, last = moved[moved.length - 1].to;
  const numbers = first === last ? `Q${first}` : `Q${first}–Q${last}`;
  return `Moved ${count(moved.length, "question")} from ${from} to ${to}: now ${numbers}.`;
}

// The folders a project covers, each with its full path (wrapped when long). Only
// folders still on disk come from the server; a renamed one is linked from the banner.
function renderFolders(project) {
  if (!project.folders.length) return null;
  return el("ul", { class: "folders", "aria-label": "Folders" }, project.folders.map((folder) =>
    el("li", {}, folder.folder, " ", el("span", { class: "path", title: folder.path }, folder.shown))));
}

// Keep the page on a renamed project, and the way back pointing at the same question.
function renamed(from, to) {
  if (state.project === from) chooseProject(to);
  const back = (state.back || "").match(/^#\/(.+)\/(\d+)$/);
  if (back && decodeURIComponent(back[1]) === from) state.back = `#/${encodeURIComponent(to)}/${back[2]}`;
}

function renderSettings(data) {
  $("#settings").replaceChildren(el("div", { class: "settings" },
    el("button", { class: "back", onclick: closeSettings }, "← Back to questions"),
    el("h2", {}, "Settings"),
    renderAppearance(),
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
  const draft = state.shown === id && $("#note")
    ? typedDraft()
    : takeDraft(id) || { text: "", choice: null };
  // A redraw of the same question keeps the note box focused, with the cursor where it was.
  const old = $("#note");
  const cursor = state.shown === id && old && document.activeElement === old
    && [old.selectionStart, old.selectionEnd, old.selectionDirection];
  state.current = key;
  state.shown = id;
  detail.replaceChildren(renderQuestion(question, draft));
  if (cursor && $("#note")) {
    $("#note").focus();
    $("#note").setSelectionRange(...cursor);
  }
}

function renderQuestion(question, draft) {
  const reworded = question.version > 1 ? `, reworded (version ${question.version})` : "";
  const parts = [
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
  const error = el("p", { class: "error", hidden: true });
  // Buttons stay disabled while a request runs, so a double click can't send twice.
  const post = (path, body) => {
    const buttons = [...document.querySelectorAll("#detail .actions button")];
    buttons.forEach((button) => { button.disabled = true; });
    return api(`/api/questions/${encodeURIComponent(question.project)}/${question.number}/${path}`, body)
      .catch((problem) => {
        buttons.forEach((button) => { button.disabled = false; });
        if ($("#send")) $("#send").disabled = !document.querySelector("input[name=choice]:checked");
        error.textContent = problem.message;
        error.hidden = false;
        throw problem;
      });
  };
  const refresh = () => { state.current = null; state.shown = null; return Promise.all([loadDetail(true), loadList()]); };
  if (question.status === "answered") {
    // Answered questions are read-only; reopening is a deliberate step, so an old tab
    // can't add a second answer by accident. The question is open again, so the page
    // moves to the Open tab with it; otherwise the next refresh drops it.
    const reopen = () => { setStatus("open"); return refresh(); };
    parts.push(el("div", { class: "actions" },
      el("button", { onclick: () => post("reopen", {}).then(reopen, () => {}) }, "Reopen to answer again")), error);
  } else if (question.status === "open") {
    parts.push(el("div", { class: "options", role: "radiogroup" },
      question.options.map((option) => el("label", {},
        el("input", { type: "radio", name: "choice", value: option.letter,
                      checked: option.letter === draft.choice }),
        el("span", { class: "letter" }, option.letter),
        el("span", { class: "text" }, option.text,
           option.letter === question.recommended && el("span", { class: "badge rec" }, "recommended"))))));
    const note = el("textarea", { id: "note",
      placeholder: "A note with your answer, or what you need to know more about" });
    note.value = draft.text;
    parts.push(note, el("div", { class: "actions" },
      el("button", { class: "primary", id: "send", disabled: !draft.choice, onclick: () => {
        const picked = document.querySelector("input[name=choice]:checked");
        post("answer", { choice: picked ? picked.value : null, body: note.value })
          .then(() => goToNextOpen(question), () => {});
      } }, reopened ? "Send new answer" : "Send answer"),
      el("button", { onclick: () => post("followup", { body: note.value }).then(refresh, () => {}) },
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
    if (event.target.name === "choice" && $("#send")) $("#send").disabled = false;
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
  rich, inline, openFor, joinNotice, serverChanged, renameBanner, renderFolders, chipProjects,
  noteOpened, questionFresh,
};
else start();
