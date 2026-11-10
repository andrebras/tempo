// The terminal under the question: a shell in the shown project's folder, so the user doesn't
// have to switch to their terminal app. Drawn by xterm.js, which ships with Tempo
// (tempo/vendor/xterm) and loads only once the terminal setting is on. Each project has its
// own shell, and the page reads its output only while it is shown: a hidden terminal holds
// no connection, as browsers allow only about six to one server. Its shell keeps running,
// in tmux or on the page server, and showing it again picks up where it was.
// Node loads this file only to test the functions it exports.

const terminals = {};
const terminalSettings = { on: false, font: "", size: 13 };
let xtermLoading = null;
// The page's own monospace font: the terminal's font unless Settings names another, and the
// fallback after the one Settings names, so a font that isn't installed still gives a grid.
const MONO = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';

function terminalSecret() {
  return document.querySelector('meta[name="tempo-secret"]').content;
}

async function terminalPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tempo-Secret": terminalSecret() },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The terminal failed");
  return data;
}

// The complete Server-Sent Events in text, each { event, data }, and the unfinished rest to
// keep for the next chunk. Comments (lines starting with ":") only say the server is there.
function sseEvents(text) {
  const blocks = text.split("\n\n");
  const rest = blocks.pop();
  const events = [];
  for (const block of blocks) {
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      const colon = line.indexOf(":");
      if (colon === 0) continue;
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

// The shell's output arrives as base64, so any bytes survive; xterm.js decodes the UTF-8.
function decodeOutput(base64) {
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}

// xterm.js colours from the page's colour tokens, so the terminal follows the light or dark
// theme. read(name) gives a CSS property's value on the page. In light colours, ANSI white
// and yellow would vanish on the white panel, so they take darker tokens.
function terminalTheme(read) {
  const theme = {
    background: read("--panel"), foreground: read("--text"),
    cursor: read("--accent"), cursorAccent: read("--panel"),
    selectionBackground: read("--accent-soft"), selectionForeground: read("--text"),
    brightBlack: read("--muted"),
  };
  if (read("color-scheme") === "dark") return theme;
  return { ...theme, white: read("--muted"), brightWhite: read("--text"), yellow: read("--now") };
}

function pageTheme() {
  const style = getComputedStyle(document.documentElement);
  return terminalTheme((name) => style.getPropertyValue(name).trim());
}

// What xterm.js gets for the saved font and size.
function terminalFont(settings) {
  return { fontFamily: settings.font ? `${settings.font}, ${MONO}` : MONO, fontSize: settings.size };
}

// The terminal's height in pixels, kept between a few lines and most of the window.
function terminalHeight(height, windowHeight) {
  return Math.round(Math.min(Math.max(height, 120), windowHeight * 0.85));
}

function setTerminalHeight(height) {
  $("#terminal").style.height = `${terminalHeight(height, innerHeight)}px`;
}

// The grip on the terminal's top edge: dragging it up makes the terminal taller. The height
// is kept per browser, like the theme.
function startGrip() {
  const grip = $("#terminal .grip");
  const keep = () => {
    try { localStorage.setItem("terminal-height", String($("#terminal").offsetHeight)); } catch { /* just don't remember */ }
  };
  try {
    const saved = Number(localStorage.getItem("terminal-height"));
    if (saved) setTerminalHeight(saved);
  } catch { /* storage blocked: keep the default */ }
  grip.addEventListener("pointerdown", (event) => {
    const startY = event.clientY;
    const startHeight = $("#terminal").offsetHeight;
    grip.setPointerCapture(event.pointerId);
    const move = (moved) => setTerminalHeight(startHeight + startY - moved.clientY);
    grip.addEventListener("pointermove", move);
    grip.addEventListener("lostpointercapture", () => {
      grip.removeEventListener("pointermove", move);
      keep();
    }, { once: true });
  });
  grip.addEventListener("keydown", (event) => {
    const step = { ArrowUp: 24, ArrowDown: -24 }[event.key];
    if (!step) return;
    event.preventDefault();
    setTerminalHeight($("#terminal").offsetHeight + step);
    keep();
  });
}

function loadXterm() {
  xtermLoading ||= new Promise((resolve, reject) => {
    document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/vendor/xterm.css" }));
    const script = (src, next) => document.head.append(Object.assign(document.createElement("script"), {
      src, onload: next, onerror: () => reject(new Error(`Could not load ${src}`)),
    }));
    script("/vendor/xterm.js", () => script("/vendor/addon-fit.js", resolve));
  });
  return xtermLoading;
}

// Why a terminal stopped reading, by the last event the server sent.
const STOPPED = {
  end: "The shell ended. Hide and show the terminal for a new one.",
  moved: "This terminal is shown in another tab now. Hide and show it to bring it back.",
  lost: "The page server went away. Hide and show the terminal to reconnect.",
};

function newTerminal(project) {
  const holder = Object.assign(document.createElement("div"), { className: "term" });
  $("#terminal .screens").append(holder);
  const term = new Terminal({ ...terminalFont(terminalSettings), theme: pageTheme(), cursorBlink: true, scrollback: 5000 });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(holder);
  // received: the bytes of output shown, so coming back asks only for the rest.
  const entry = { project, holder, term, fit, id: null, folder: "", session: null, received: 0, reading: null };
  terminals[project] = entry;
  term.onData((data) => sendTerminal(entry, "input", { data }));
  term.onResize(({ cols, rows }) => sendTerminal(entry, "resize", { cols, rows }));
  return entry;
}

function sendTerminal(entry, path, body) {
  if (entry.id) terminalPost(`/api/terminal/${entry.id}/${path}`, body).catch(() => {});
}

// Gets the project's shell from the server, the running one or a new one, and reads its
// output. A new shell, or a new tmux client after a server restart, starts on a clean screen.
async function attachTerminal(entry) {
  const reading = new AbortController();
  entry.reading = reading;
  let shell;
  try {
    shell = await terminalPost("/api/terminal", { project: entry.project });
  } catch (error) {
    if (entry.reading === reading) entry.reading = null;
    entry.term.write(`${error.message}\r\n`);
    return;
  }
  if (entry.reading !== reading) return; // hidden in the meantime
  if (shell.id !== entry.id) {
    if (entry.id) entry.term.reset();
    entry.received = 0;
  }
  Object.assign(entry, { id: shell.id, folder: shell.folder, session: shell.session });
  readOutput(entry, reading);
  fitTerminal(entry);
  sendTerminal(entry, "resize", { cols: entry.term.cols, rows: entry.term.rows });
}

// Stops reading a hidden terminal's output, which frees its connection; the shell goes on.
function detachTerminal(entry) {
  const reading = entry.reading;
  entry.reading = null;
  reading?.abort();
}

// Reads the shell's output after what is shown already, until the page hides it or it
// stops. fetch, not EventSource, because every request must send the secret in a header,
// and because the page decides when to read again: when the terminal is shown.
async function readOutput(entry, reading) {
  let last = "lost";
  try {
    const response = await fetch(`/api/terminal/${entry.id}/output?since=${entry.received}`, {
      headers: { "X-Tempo-Secret": terminalSecret() }, signal: reading.signal,
    });
    if (!response.ok) throw new Error((await response.json()).error);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let rest = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const parsed = sseEvents(rest + decoder.decode(value, { stream: true }));
      rest = parsed.rest;
      for (const { event, data } of parsed.events) {
        if (event !== "message") {
          last = event;
          continue;
        }
        const bytes = decodeOutput(data);
        entry.received += bytes.length;
        entry.term.write(bytes);
      }
    }
  } catch { /* hidden, or the server went away */ }
  if (entry.reading !== reading) return; // hidden, or reading again
  entry.reading = null;
  if (terminals[entry.project] === entry) entry.term.write(`\r\n[${STOPPED[last] || STOPPED.lost}]\r\n`);
}

// Where the shell lives, for the bar: a tmux session the user can attach to from their own
// terminal too, or the page server, which keeps it until it is ended.
function terminalMode(entry) {
  if (!entry.id) return "";
  return entry.session ? `tmux: ${entry.session} · tmux attach -t ${entry.session}` : "kept alive by the page server";
}

function fitTerminal(entry) {
  try { entry.fit.fit(); } catch { /* hidden: it fits when shown */ }
}

function setTerminalButton(open) {
  if (!open) for (const entry of Object.values(terminals)) detachTerminal(entry);
  $("#terminal").hidden = !open;
  $("#terminal-toggle").setAttribute("aria-pressed", String(open));
}

async function showTerminal() {
  setTerminalButton(true);
  const project = state.project;
  for (const entry of Object.values(terminals)) {
    entry.holder.hidden = entry.project !== project;
    if (entry.project !== project) detachTerminal(entry);
  }
  $("#terminal .name").textContent = project;
  const entry = terminals[project] || newTerminal(project);
  if (!entry.reading) await attachTerminal(entry);
  if (state.project !== project) return; // another project was picked meanwhile
  $("#terminal .where").textContent = entry.folder;
  $("#terminal .mode").textContent = terminalMode(entry);
  fitTerminal(entry);
  entry.term.focus();
}

function endTerminal() {
  const entry = terminals[state.project];
  if (entry) {
    delete terminals[state.project];
    detachTerminal(entry);
    sendTerminal(entry, "close", {});
    entry.term.dispose();
    entry.holder.remove();
  }
  setTerminalButton(false);
}

// page.js calls this each time it picks the project, also when it stays the same (each
// timed refresh), so an open panel moves to the new project's shell only on a change.
// biome-ignore lint/correctness/noUnusedVariables: page.js calls it, from another script
function followTerminal() {
  if (!$("#terminal").hidden && $("#terminal .name").textContent !== state.project) showTerminal();
}

// page.js calls this when the page theme changes; the system's own change comes from below.
function rethemeTerminals() {
  const theme = pageTheme();
  for (const entry of Object.values(terminals)) entry.term.options.theme = theme;
}

// Settings' Terminal card saved (or the page started): show or hide the button, and give
// open terminals the new font. Turning it off in Settings stopped every shell already.
async function applyTerminalSettings(data) {
  Object.assign(terminalSettings, { on: data.terminal, font: data.terminal_font, size: data.terminal_size });
  if (!data.terminal) {
    for (const entry of Object.values(terminals)) { detachTerminal(entry); entry.term.dispose(); entry.holder.remove(); }
    for (const project of Object.keys(terminals)) delete terminals[project];
    setTerminalButton(false);
  }
  try {
    if (data.terminal) await loadXterm();
  } catch {
    return; // no button without xterm.js
  }
  $("#terminal-toggle").hidden = !data.terminal;
  for (const entry of Object.values(terminals)) {
    Object.assign(entry.term.options, terminalFont(terminalSettings));
    fitTerminal(entry);
  }
}

function startTerminal() {
  $("#terminal-toggle").addEventListener("click", () => {
    if ($("#terminal").hidden) showTerminal();
    else setTerminalButton(false);
  });
  $("#terminal .hide").addEventListener("click", () => setTerminalButton(false));
  $("#terminal .end").addEventListener("click", endTerminal);
  startGrip();
  new ResizeObserver(() => {
    const entry = terminals[state.project];
    if (entry && !$("#terminal").hidden) fitTerminal(entry);
  }).observe($("#terminal .screens"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rethemeTerminals);
  fetch("/api/settings").then((response) => response.json()).then(applyTerminalSettings).catch(() => {});
}

if (typeof module === "object") module.exports = { sseEvents, decodeOutput, terminalTheme, terminalFont, terminalHeight, terminalMode, MONO };
else startTerminal();
