// Tests for the page's text renderer, rich() and inline(), and for other helpers, run with
// `node --test`. rich() and inline() turn a little Markdown into DOM nodes; a tiny fake
// document stands in for the browser's, and refuses innerHTML so any attempt to inject
// markup fails loudly.
const assert = require("node:assert/strict");
const { test } = require("node:test");

class Node {}

class Text extends Node {
  constructor(text) {
    super();
    this.textContent = text;
  }
}

class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.className = "";
    this.attributes = {};
    this.childNodes = [];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener() {}

  append(...children) {
    for (const child of children) {
      this.childNodes.push(child instanceof Node ? child : new Text(String(child)));
    }
  }

  get textContent() {
    return this.childNodes.map((child) => child.textContent).join("");
  }

  set innerHTML(_html) {
    throw new Error("innerHTML must not be used");
  }
}

globalThis.Node = Node;
globalThis.document = { createElement: (tag) => new Element(tag) };

const {
  rich, inline, openFor, joinNotice, mergeNotice, mergeQuestion, mergeForm, serverChanged, renderFolders, chipProjects,
  pathParts, noteOpened, questionFresh, keptDraft, renderQuestion, renderSessions, rewordNotice, state,
} = require("../tempo/page.js");

// A compact picture of a node tree: text as strings, elements as [TAG, ...children].
// inline() returns plain strings for its text parts; inside elements they are Text nodes.
function shape(node) {
  if (typeof node === "string") return node;
  if (node instanceof Text) return node.textContent;
  return [node.tagName, ...node.childNodes.map(shape)];
}

function render(text) {
  return rich(text).childNodes.map(shape);
}

function all(node, tag) {
  if (!(node instanceof Element)) return [];
  const found = node.tagName === tag ? [node] : [];
  return found.concat(...node.childNodes.map((child) => all(child, tag)));
}

test("blank lines separate paragraphs, single newlines become line breaks", () => {
  assert.deepEqual(render("First line\nsame paragraph\n\nSecond paragraph"), [
    ["P", "First line", ["BR"], "same paragraph"],
    ["P", "Second paragraph"],
  ]);
});

test("dash and star lines become a bullet list", () => {
  assert.deepEqual(render("- one\n* two"), [["UL", ["LI", "one"], ["LI", "two"]]]);
});

test("numbered lines become a numbered list", () => {
  assert.deepEqual(render("1. one\n2) two"), [["OL", ["LI", "one"], ["LI", "two"]]]);
});

test("a lead line followed by a list keeps both, in order", () => {
  assert.deepEqual(render("Pick one:\n- left\n- right"), [
    ["P", "Pick one:"],
    ["UL", ["LI", "left"], ["LI", "right"]],
  ]);
});

test("bold, and code inside bold", () => {
  assert.deepEqual(inline("a **bold** word").map(shape), ["a ", ["STRONG", "bold"], " word"]);
  assert.deepEqual(inline("**run `bin/ci` first**").map(shape), [
    ["STRONG", "run ", ["CODE", "bin/ci"], " first"],
  ]);
});

test("http and https links become links that open in a new tab", () => {
  for (const url of ["http://example.com/a", "https://example.com/b?c=1"]) {
    const [, link, after] = inline(`see ${url}.`);
    assert.equal(link.tagName, "A");
    assert.equal(link.attributes.href, url);
    assert.equal(link.attributes.target, "_blank");
    assert.equal(link.attributes.rel, "noopener noreferrer");
    assert.equal(link.textContent, url);
    assert.equal(after, ".");
  }
});

test("javascript: links stay text", () => {
  const holder = rich("click javascript:alert(1)");
  assert.deepEqual(all(holder, "A"), []);
  assert.equal(holder.textContent, "click javascript:alert(1)");
});

test("HTML stays text", () => {
  const text = '<script>alert("hi")</script> and <b onclick="x()">bold</b>';
  const holder = rich(text);
  assert.deepEqual(all(holder, "SCRIPT"), []);
  assert.deepEqual(all(holder, "B"), []);
  assert.deepEqual(shape(holder), ["DIV", ["P", text]]);
});

test("openFor says how long a question has been open, in the largest whole unit", () => {
  const now = Date.parse("2026-11-07T12:00:00Z");
  const cases = {
    "2026-11-07T11:59:55Z": "open 1 minute",
    "2026-11-07T11:01:00Z": "open 59 minutes",
    "2026-11-07T09:55:00Z": "open 2 hours",
    "2026-11-06T12:00:00Z": "open 1 day",
    "2026-11-03T13:00:00Z": "open 3 days",
  };
  for (const [createdAt, text] of Object.entries(cases)) assert.equal(openFor(createdAt, now), text);
});

test("joinNotice says how many questions moved and their new numbers", () => {
  assert.equal(joinNotice("a", "b", []), null);
  assert.equal(joinNotice("tempo", "Tempo", [{ from: 2, to: 28 }]), "Moved 1 question from tempo to Tempo: now Q28.");
  assert.equal(
    joinNotice("tempo", "Tempo", [{ from: 1, to: 28 }, { from: 2, to: 29 }]),
    "Moved 2 questions from tempo to Tempo: now Q28–Q29.",
  );
});

test("mergeNotice gives the new numbers, or how many questions moved when they kept theirs", () => {
  assert.equal(mergeNotice("old", "new", 1, [{ from: 1, to: 3 }]), "Moved 1 question from old to new: now Q3.");
  assert.equal(mergeNotice("old", "new", 2, []), "Moved 2 questions from old to new, with the same numbers.");
  assert.equal(mergeNotice("old", "new", 0, []), "Merged old into new.");
});

test("mergeQuestion says what moves, and warns of new numbers only when both have questions", () => {
  const old = { name: "old", questions: 2 };
  assert.equal(mergeQuestion(old, { name: "new", questions: 0 }),
    "Merge old into new? Its 2 questions and folders move to new, and old goes away.");
  assert.match(mergeQuestion(old, { name: "new", questions: 5 }), /They get numbers after new's/);
  assert.match(mergeQuestion({ name: "old", questions: 0 }, { name: "new", questions: 5 }), /\? Its folders move/);
});

test("the merge form lists every other project, and waits for a pick", () => {
  const projects = [{ name: "old", questions: 1 }, { name: "new", questions: 2 }, { name: "shop", questions: 0 }];
  const [form, confirm] = mergeForm(projects[0], projects);
  const select = all(form, "SELECT")[0];
  assert.equal(select.attributes["aria-label"], "Merge old into");
  assert.equal(select.attributes.disabled, undefined);
  assert.deepEqual(all(select, "OPTION").map((option) => option.attributes.value), ["", "new", "shop"]);
  const button = all(form, "BUTTON")[0];
  assert.equal(button.textContent, "Merge…");
  assert.equal(button.attributes.disabled, "");
  assert.equal(confirm.attributes.hidden, "");
  assert.equal(confirm.attributes.role, "group");
});

test("with no other project the merge list is switched off and says why", () => {
  const [form] = mergeForm({ name: "only", questions: 1 }, [{ name: "only", questions: 1 }]);
  const select = all(form, "SELECT")[0];
  assert.equal(select.attributes.disabled, "");
  assert.equal(select.textContent, "No other project to merge into");
});

test("serverChanged notices a restarted server, not a missing answer", () => {
  const first = { pid: 1, started: "2026-11-07T11:03:27Z" };
  assert.equal(serverChanged(first, { ...first }), false);
  assert.equal(serverChanged(first, { ...first, pid: 2 }), true);
  assert.equal(serverChanged(first, { ...first, started: "2026-11-07T12:00:00Z" }), true);
  assert.equal(serverChanged(null, first), false);
  assert.equal(serverChanged(first, null), false);
});

const tempo = { folder: "tempo", path: "/lab/tempo", shown: "~/lab/tempo", project: "tempo" };

test("folders are listed read-only, with no menu to move them", () => {
  const list = renderFolders({ name: "tempo", folders: [tempo] });
  assert.equal(list.textContent, "tempo ~/lab/tempo");
  assert.deepEqual(all(list, "SELECT"), []);
  assert.deepEqual(all(list, "BUTTON"), []);
  assert.equal(renderFolders({ name: "tempo", folders: [] }), null);
});

test("a folder that no longer exists says so", () => {
  const list = renderFolders({ name: "tempo", folders: [tempo, { ...tempo, shown: "~/old/tempo", gone: true }] });
  assert.deepEqual(all(list, "LI").map((item) => item.textContent),
    ["tempo ~/lab/tempo", "tempo ~/old/tempo" + "folder gone"]);
});

test("a path can break only after a /", () => {
  assert.deepEqual(pathParts("~/Lab/tempo").map(shape), ["~/", ["WBR"], "Lab/", ["WBR"], "tempo"]);
  assert.deepEqual(pathParts("tempo").map(shape), ["tempo"]);
  assert.deepEqual(pathParts(null), [""]);
});

test("the page switch shows whether each session starts the page", () => {
  for (const [on, pressed] of [[true, ["true", "false"]], [false, ["false", "true"]]]) {
    const card = renderSessions({ start_page: on });
    const buttons = all(card, "BUTTON");
    assert.deepEqual(buttons.map((button) => button.textContent), ["On", "Off"]);
    assert.deepEqual(buttons.map((button) => button.attributes["aria-pressed"]), pressed);
    assert.match(card.textContent, /Start the page with each session/);
  }
});

test("chips show projects with open questions or news, and always the current one", () => {
  const projects = ["idle", "busy", "fresh", "here"];
  const counts = { busy: 2 };
  const activity = { idle: "2026-01-01T00:00:00Z", busy: "2026-01-01T00:00:00Z",
    fresh: "2026-01-03T00:00:00Z" };
  const seen = { idle: "2026-01-01T00:00:00Z", busy: "2026-01-02T00:00:00Z", fresh: "2026-01-02T00:00:00Z" };
  assert.deepEqual(chipProjects(projects, counts, activity, seen, "here"), [
    { name: "busy", open: 2, current: false, fresh: false },
    { name: "fresh", open: 0, current: false, fresh: true },
    { name: "here", open: 0, current: true, fresh: false },
  ]);
});

test("a project never viewed is new once it has activity; the current one never shows the dot", () => {
  const activity = { other: "2026-01-01T00:00:00Z", here: "2026-01-05T00:00:00Z" };
  const chips = chipProjects(["other", "here", "quiet"], { here: 1 }, activity, {}, "here");
  assert.deepEqual(chips.map(({ name, fresh }) => [name, fresh]), [["other", true], ["here", false]]);
  assert.deepEqual(chipProjects(["other"], {}, activity, null, "").map((chip) => chip.fresh), [true]);
});

// Question dots: an open question in the list is dotted when Claude changed it since the
// user last opened it.
const question = (number, activity, extra = {}) =>
  ({ project: "demo", number, status: "open", waiting: null, activity, ...extra });
const day = (n) => `2026-01-0${n}T00:00:00Z`;

test("a first visit counts every open question as opened, so none is dotted", () => {
  const open = [question(1, day(1)), question(2, day(2))];
  const opened = noteOpened({}, ["demo"], open, "demo", null);
  assert.deepEqual(opened, { demo: { 1: day(1), 2: day(2) } });
  assert.deepEqual(open.map((q) => questionFresh(q, opened)), [false, false]);
  // A project never visited shows no dots either.
  assert.equal(questionFresh(question(1, day(1), { project: "other" }), opened), false);
});

test("a question that arrives after the first visit is dotted until it is opened", () => {
  let opened = noteOpened({}, ["demo"], [question(1, day(1))], "demo", null);
  const open = [question(1, day(1)), question(2, day(2))];
  opened = noteOpened(opened, ["demo"], open, "demo", null);
  assert.deepEqual(open.map((q) => questionFresh(q, opened)), [false, true]);
  opened = noteOpened(opened, ["demo"], open, "demo", { project: "demo", number: 2 });
  assert.equal(questionFresh(open[1], opened), false);
});

test("a reword or Claude's context after the last open dots the question", () => {
  // Either one moves the question's activity time on the server (see test_store.py).
  const opened = noteOpened({}, ["demo"], [question(1, day(1)), question(2, day(1))], "demo", null);
  const reworded = question(1, day(3));
  const withContext = question(2, day(4));
  const later = noteOpened(opened, ["demo"], [reworded, withContext], "demo", null);
  assert.deepEqual([reworded, withContext].map((q) => questionFresh(q, later)), [true, true]);
  assert.equal(questionFresh(question(2, day(1)), later), false);
});

test("opening the question since the change clears its dot, and a wait hides it", () => {
  const changed = question(1, day(3));
  const opened = noteOpened({ demo: { 1: day(1) } }, ["demo"], [changed], "demo", { project: "demo", number: 1 });
  assert.equal(questionFresh(changed, opened), false);
  assert.equal(questionFresh(question(1, day(3), { waiting: "sent" }), { demo: { 1: day(1) } }), false);
  assert.equal(questionFresh(question(1, day(3), { status: "answered" }), { demo: { 1: day(1) } }), false);
});

test("the record drops questions no longer open and projects that are gone", () => {
  const opened = { demo: { 1: day(1), 2: day(1) }, gone: { 1: day(1) } };
  assert.deepEqual(noteOpened(opened, ["demo"], [question(1, day(1))], "demo", null), { demo: { 1: day(1) } });
});

// The question screen. A redraw keeps what the user picked and typed, but a pick is kept
// only for the wording it was made on.
const openQuestion = (extra = {}) => ({
  project: "demo", number: 1, label: "Q1", status: "open", version: 2, title: "Which way?",
  goal: null, start_here: null, context: "We need to pick one.", answer: null, recommended: null,
  options: [{ letter: "a", text: "Left" }, { letter: "b", text: "Right" }],
  versions: [{ created_at: day(1), options: [] }, { created_at: day(2), options: [] }],
  entries: [], created_at: day(1), ...extra,
});

test("a pick made on an older wording is dropped; the note stays", () => {
  assert.deepEqual(keptDraft({ text: "hi", choice: "b", version: 1 }, openQuestion()),
    { text: "hi", choice: null, version: 2 });
  assert.deepEqual(keptDraft({ text: "hi", choice: "b", version: 2 }, openQuestion()),
    { text: "hi", choice: "b", version: 2 });
});

test("a reword while the user is answering says what was kept and what was cleared", () => {
  assert.equal(rewordNotice({ text: "", choice: null, version: 1 }, openQuestion()), null);
  assert.equal(rewordNotice({ text: "hi", choice: "b", version: 2 }, openQuestion()), null);
  assert.equal(rewordNotice({ text: "hi", choice: "b", version: 1 }, openQuestion()),
    "Claude reworded this question while you were answering it (now version 2)."
    + " Your pick was cleared: read the options again. Your note is kept.");
  assert.equal(rewordNotice({ text: " ", choice: "a", version: 1 }, openQuestion()),
    "Claude reworded this question while you were answering it (now version 2)."
    + " Your pick was cleared: read the options again.");
});

test("while an answer is being sent, a redraw keeps the buttons disabled", () => {
  const buttons = () => all(renderQuestion(openQuestion(), { text: "", choice: "a", version: 2 }), "BUTTON")
    .map((button) => [button.textContent, "disabled" in button.attributes]);
  assert.deepEqual(buttons(), [["Send answer", false], ["Ask for more context", false]]);
  state.sending = "demo/1";
  try {
    assert.deepEqual(buttons(), [["Send answer", true], ["Ask for more context", true]]);
  } finally {
    state.sending = null;
  }
});
