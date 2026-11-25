// Tempo in Herdr: the open questions of the shown space's project, beside the terminals.
// A click opens the question in Tempo's own terminal view (`tempo tui --question N`), in a
// dialog that closes once the question is answered. Only the person answers: this mod
// never runs `tempo answer` or `tempo reply`. A toast says when a new question arrives.
//
// It runs programs: `tempo list --json` every few seconds (less often while the window is
// hidden), and `hc run` to start the answering terminal.

import { View, div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import {
  checking,
  exec,
  hc,
  home,
  nextCommand,
  notify,
  openOverlay,
  panes,
  setShared,
  shared,
  spaces,
  theme,
  window,
} from "herdr";

// How often to read the questions while the window shows, and while it is hidden.
const SHOWN_MS = 4000;
const HIDDEN_MS = 30000;
// The newest question already announced, shared so copies and reloads never repeat a toast.
const ANNOUNCED = "tempo.announced";

export default class Tempo extends View {
  init(_props, cx) {
    this.tempo = null; // the tempo command's path, once found
    this.missing = false; // no tempo command on this machine
    this.all = []; // every project's open questions
    this.mine = null; // the shown space's: { cwd, project, questions }
    this.error = null;
    this.busy = false;
    this.last = 0;
    if (checking()) return;
    cx.spawn(async (cx) => {
      this.tempo = await findTempo();
      this.missing = !this.tempo;
      await this.refresh(cx);
    });
    cx.timer.every(SHOWN_MS, (cx) => {
      const wait = window()?.visible === false ? HIDDEN_MS : SHOWN_MS;
      if (Date.now() - this.last >= wait - 100) cx.spawn((cx) => this.refresh(cx));
    });
    cx.spawn(async (cx) => {
      for (;;) {
        const command = await nextCommand();
        await this.command(command, cx);
        cx.notify();
      }
    });
  }

  // "open": the shown project's first question; "open:N:project": that one.
  async command(command, cx) {
    const [name, number, ...rest] = command.split(":");
    if (name !== "open") return;
    if (number) return this.answer(rest.join(":"), Number(number), cx);
    const first = this.mine?.questions[0] ?? this.all[0];
    if (first) return this.answer(first.project, first.number, cx);
    notify({ title: "Tempo", body: "No open questions.", level: "info", timeout: 3000 });
  }

  async refresh(cx) {
    if (!this.tempo || this.busy) return;
    this.busy = true;
    this.last = Date.now();
    const cwd = shownFolder();
    try {
      const all = await list(this.tempo, ["--all-projects"], home());
      const mine = cwd ? await list(this.tempo, [], cwd) : null;
      this.announce(all);
      const changed =
        JSON.stringify(all) !== JSON.stringify(this.all) ||
        JSON.stringify(mine) !== JSON.stringify(this.mine?.questions) ||
        cwd !== this.mine?.cwd;
      this.all = all;
      this.mine = cwd ? { cwd, questions: mine, project: mine[0]?.project ?? null } : null;
      this.error = null;
      if (changed) cx.notify();
    } catch (error) {
      if (this.error !== String(error)) {
        this.error = String(error);
        cx.notify();
      }
    } finally {
      this.busy = false;
    }
  }

  // Toast the questions asked since the last toast; the first read only sets the mark.
  announce(all) {
    const newest = all.reduce((max, q) => (q.created_at > max ? q.created_at : max), "");
    const mark = shared(ANNOUNCED);
    if (typeof mark === "string") {
      const fresh = all.filter((q) => q.created_at > mark);
      for (const q of fresh.slice(-3)) {
        notify({
          title: `Tempo: ${q.label} in ${q.project}`,
          body: q.title,
          level: "info",
          timeout: 8000,
          action: { name: `tempo.open:${q.number}:${q.project}` },
        });
      }
    }
    if (newest && newest !== mark && (typeof mark !== "string" || newest > mark)) {
      setShared(ANNOUNCED, newest);
    }
  }

  // Open one question in Tempo's terminal view, in a dialog that closes with it.
  async answer(project, number, _cx) {
    if (!this.tempo) return;
    const argv = [this.tempo, "tui", "--question", String(number), "--project", project];
    const r = await hc(["run", "--json", "--bg", "--rm", "--no-parent", "--cwd", home(), "--", ...argv]);
    let address = null;
    try {
      address = JSON.parse(r.stdout).address;
    } catch {
      address = null;
    }
    if (r.code !== 0 || !address) {
      notify({ title: "Tempo", body: `Couldn't open Q${number}: ${r.stderr.trim()}`, level: "error" });
      return;
    }
    // Modal, so only Tempo's own keys close it (Esc, h, q, or an answer): the dialog
    // closes when its terminal ends, and no terminal is left running behind it.
    openOverlay({
      kind: "dialog",
      modal: true,
      width: 0.7,
      height: 0.75,
      layout: { type: "block", content: { terminal: address } },
    });
  }

  render(cx) {
    const t = theme();
    const cwd = shownFolder();
    if (this.tempo && cwd !== this.mine?.cwd && !this.busy) cx.spawn((cx) => this.refresh(cx));
    const mine = this.mine?.questions ?? [];
    const project = this.mine?.project;
    const others = groupBy(
      this.all.filter((q) => q.project !== project),
      (q) => q.project,
    );
    return v_flex()
      .id("root")
      .size_full()
      .bg(t.chrome)
      .text_size(13)
      .text_color(t.foreground)
      .child(
        h_flex()
          .px(12)
          .py(10)
          .gap(8)
          .items_center()
          .border_b(1)
          .border_color(t.border)
          .child(div().font_semibold().child("Questions"))
          .child(div().flex_1().truncate().text_color(t.muted).child(project ?? ""))
          .when(mine.length > 0, (el) =>
            el.child(
              div()
                .px(6)
                .rounded(8)
                .bg(t.blocked)
                .text_size(11)
                .text_color(t.solid_foreground)
                .child(String(mine.length)),
            ),
          ),
      )
      .child(
        v_flex()
          .id("tempo-list")
          .flex_1()
          .min_h(0)
          .overflow_y_scrollbar()
          .p(8)
          .gap(2)
          .children([
            ...this.status(t, cwd, mine),
            ...goals(mine).flatMap(([goal, qs]) => [
              ...(goal ? [heading(t, goal)] : []),
              ...qs.map((q) => this.row(t, q)),
            ]),
            ...(others.length ? [heading(t, "Other projects")] : []),
            ...others.map(([name, qs]) => this.projectRow(t, name, qs)),
          ]),
      );
  }

  // A line saying why the list is empty, if it is.
  status(t, cwd, mine) {
    let text = null;
    if (checking()) text = "";
    else if (this.missing) text = "The tempo command wasn't found. Install it with /tempo:install.";
    else if (this.error) text = this.error;
    else if (!this.tempo || !this.mine) text = cwd ? "Reading…" : "This space isn't on this computer.";
    else if (mine.length === 0) text = "No open questions here.";
    if (text === null) return [];
    return [div().px(4).py(6).text_size(12).text_color(t.muted).child(text)];
  }

  row(t, q) {
    return h_flex()
      .id(`q-${q.project}-${q.number}`)
      .gap(8)
      .px(6)
      .py(5)
      .rounded(6)
      .items_start()
      .cursor_pointer()
      .hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => cx.spawn((cx) => this.answer(q.project, q.number, cx)))
      .child(div().flex_none().w(34).text_color(q.waiting ? t.working : t.accent).child(q.label))
      .child(div().flex_1().min_w(0).child(q.title));
  }

  projectRow(t, name, qs) {
    return h_flex()
      .id(`p-${name}`)
      .gap(8)
      .px(6)
      .py(4)
      .rounded(6)
      .cursor_pointer()
      .hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => cx.spawn((cx) => this.answer(name, qs[0].number, cx)))
      .child(div().flex_1().truncate().child(name))
      .child(div().text_color(t.muted).child(String(qs.length)));
  }
}

// The folder of the shown tab's focused terminal pane, or of its first one; null
// for a space on another machine.
function shownFolder() {
  const space = spaces().find((s) => s.active);
  if (!space || space.machine) return null;
  // Panes only: a terminal in a dialog (such as this mod's own) is focused but has no pane.
  const tab = space.tabs.find((tab) => tab.active);
  const shown = panes().filter((p) => p.tab === tab?.id && p.cwd);
  const pane = shown.find((p) => p.focused) ?? shown[0];
  return pane?.cwd ?? (space.folder || home());
}

async function findTempo() {
  const r = await exec("/bin/sh", ["-c", "command -v tempo"]).catch(() => null);
  const found = r?.code === 0 ? r.stdout.trim() : "";
  if (found) return found;
  const fallback = `${home()}/.local/bin/tempo`;
  const test = await exec("/bin/test", ["-x", fallback]).catch(() => null);
  return test?.code === 0 ? fallback : null;
}

async function list(tempo, args, cwd) {
  const r = await exec(tempo, ["list", "--json", ...args], { cwd, timeout: 15000 });
  if (r.code !== 0) throw new Error(r.stderr.trim() || `tempo list failed (${r.code})`);
  return JSON.parse(r.stdout);
}

// Questions by goal, keeping tempo's order; questions without a goal come last.
function goals(questions) {
  const groups = groupBy(questions, (q) => q.goal ?? "");
  return [...groups.filter(([g]) => g), ...groups.filter(([g]) => !g)];
}

function groupBy(items, key) {
  const groups = new Map();
  for (const item of items) {
    const k = key(item);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(item);
  }
  return [...groups.entries()];
}

function heading(t, text) {
  return div().px(6).pt(8).pb(2).text_size(11).text_color(t.dim).truncate().child(text);
}
