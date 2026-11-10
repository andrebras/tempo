// Tests for the terminal's helpers in tempo/terminal.js, run with `node --test`: reading the
// shell's output as Server-Sent Events, decoding it, the colours and font xterm.js gets, and
// where the bar says the shell lives.
const assert = require("node:assert/strict");
const { test } = require("node:test");

const { sseEvents, decodeOutput, terminalTheme, terminalFont, terminalHeight, terminalMode, MONO } = require("../tempo/terminal.js");

test("complete events are read and an unfinished one is kept for the next chunk", () => {
  const { events, rest } = sseEvents("data: aGk=\n\n: still here\n\nevent: end\ndata:\n\ndata: bW9y");
  assert.deepEqual(events, [{ event: "message", data: "aGk=" }, { event: "end", data: "" }]);
  assert.equal(rest, "data: bW9y");
  assert.deepEqual(sseEvents(`${rest}ZQ==\n\n`).events, [{ event: "message", data: "bW9yZQ==" }]);
});

test("a chunk with no complete event gives none", () => {
  assert.deepEqual(sseEvents("dat"), { events: [], rest: "dat" });
  assert.deepEqual(sseEvents(""), { events: [], rest: "" });
});

test("output keeps every byte, including UTF-8 split across chunks", () => {
  assert.deepEqual([...decodeOutput("aGkK")], [104, 105, 10]);
  // "é" is two bytes; xterm.js joins them across writes.
  assert.deepEqual([...decodeOutput(Buffer.from("é").toString("base64"))], [0xc3, 0xa9]);
});

const light = {
  "--panel": "#ffffff", "--text": "#23211c", "--accent": "#4f46e5", "--accent-soft": "#eeedfd",
  "--muted": "#6b665e", "--now": "#c2410c", "color-scheme": "light",
};
const dark = { ...light, "--panel": "#1d1c24", "--text": "#ecebf2", "--muted": "#9b98a8", "color-scheme": "dark" };

test("the terminal takes the page's colours", () => {
  const theme = terminalTheme((name) => light[name]);
  assert.equal(theme.background, "#ffffff");
  assert.equal(theme.foreground, "#23211c");
  assert.equal(theme.cursor, "#4f46e5");
  assert.equal(theme.selectionBackground, "#eeedfd");
  assert.equal(terminalTheme((name) => dark[name]).background, "#1d1c24");
});

test("in light colours, white and yellow text stay readable on the white panel", () => {
  const theme = terminalTheme((name) => light[name]);
  assert.deepEqual([theme.white, theme.brightWhite, theme.yellow], ["#6b665e", "#23211c", "#c2410c"]);
  const night = terminalTheme((name) => dark[name]);
  assert.deepEqual([night.white, night.brightWhite, night.yellow], [undefined, undefined, undefined]);
});

test("the font is the page's monospace one unless Settings names another, then its fallback", () => {
  assert.deepEqual(terminalFont({ font: "", size: 13 }), { fontFamily: MONO, fontSize: 13 });
  assert.deepEqual(terminalFont({ font: '"Fira Code"', size: 16 }), { fontFamily: `"Fira Code", ${MONO}`, fontSize: 16 });
  assert.match(MONO, /ui-monospace/);
});

test("the dragged height stays between a few lines and most of the window", () => {
  assert.equal(terminalHeight(300, 1000), 300);
  assert.equal(terminalHeight(40, 1000), 120);
  assert.equal(terminalHeight(2000, 1000), 850);
});

test("the bar says where the shell lives: its tmux session, or the page server", () => {
  const tmux = "tmux: tempo-app-1a2b3c · tmux attach -t tempo-app-1a2b3c";
  assert.equal(terminalMode({ id: "k", session: "tempo-app-1a2b3c" }), tmux);
  assert.equal(terminalMode({ id: "k", session: null }), "kept alive by the page server");
  assert.equal(terminalMode({ id: null, session: null }), "");
});
