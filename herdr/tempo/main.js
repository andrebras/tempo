// Tempo in Herdr: the open questions of the shown space's project, beside the terminals.
// A click shows the question in this panel: its context, its options and its thread. You
// pick an option (with an optional note) and send it, or ask Claude for more. These are the
// person's own clicks, sent with `tempo answer` and `tempo reply`, as the page sends them.
// A toast says when a new question arrives.
//
// It runs programs: `tempo list --json` every few seconds (less often while the window is
// hidden), `tempo show --json` for the open question, and `tempo answer` or `tempo reply`
// when you send.

import { View, div } from "gpui-kit";
import { h_flex, Input, InputState, TextView, v_flex } from "gpui-base";
import {
  checking,
  exec,
  home,
  nextCommand,
  notify,
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
    this.open = null; // the question shown in full: { project, number, question, pick }
    this.sending = false;
    this.note = InputState.new({ placeholder: "Add a note (optional)" });
    this.more = InputState.new({ placeholder: "Ask Claude for more…" });
    this.more.on("submit", (_e, cx) => cx.spawn((cx) => this.followUp(cx)));
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
    if (number) return this.show(rest.join(":"), Number(number), cx);
    const first = this.mine?.questions[0] ?? this.all[0];
    if (first) return this.show(first.project, first.number, cx);
    notify({ title: "Tempo", body: "No open questions.", level: "info", timeout: 3000 });
  }

  async refresh(cx) {
    if (!this.tempo || this.busy) return;
    this.busy = true;
    this.last = Date.now();
    const cwd = shownFolder();
    try {
      const all = await tempo(this.tempo, ["list", "--json", "--all-projects"], home());
      const mine = cwd ? await tempo(this.tempo, ["list", "--json"], cwd) : null;
      this.announce(all);
      const changed =
        JSON.stringify(all) !== JSON.stringify(this.all) ||
        JSON.stringify(mine) !== JSON.stringify(this.mine?.questions) ||
        cwd !== this.mine?.cwd;
      this.all = all;
      this.mine = cwd ? { cwd, questions: mine, project: mine[0]?.project ?? null } : null;
      this.error = null;
      if (this.open) await this.follow(cx);
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

  // Keep the open question current: reread it when it changed, close it once it isn't open.
  async follow(cx) {
    const { project, number, question } = this.open;
    const now = this.all.find((q) => q.project === project && q.number === number);
    if (!now) {
      this.open = null;
      cx.notify();
    } else if (now.updated_at !== question?.updated_at || now.waiting !== question?.waiting) {
      await this.load(cx);
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
    if (newest && (typeof mark !== "string" || newest > mark)) setShared(ANNOUNCED, newest);
  }

  async show(project, number, cx) {
    this.open = { project, number, question: null, pick: null };
    this.note.set_value("");
    this.more.set_value("");
    cx.notify();
    await this.load(cx);
  }

  async load(cx) {
    const { project, number } = this.open ?? {};
    if (!this.tempo || !project) return;
    try {
      const question = await tempo(this.tempo, ["show", String(number), "--json", "--project", project], home());
      const list = this.all.find((q) => q.project === project && q.number === number);
      if (this.open?.project === project && this.open.number === number) {
        // waiting comes from the list: show --json doesn't carry it.
        this.open.question = { ...question, waiting: list?.waiting ?? null };
      }
    } catch (error) {
      notify({ title: "Tempo", body: String(error), level: "error" });
      this.open = null;
    }
    cx.notify();
  }

  // The person's own answer, sent as the page sends it.
  async send(cx) {
    const { project, number, pick } = this.open ?? {};
    if (!pick || this.sending) return;
    this.sending = true;
    cx.notify();
    const args = ["answer", String(number), pick, "--project", project];
    const note = this.note.value().trim();
    if (note) args.push("--note", note);
    const r = await exec(this.tempo, args, { cwd: home() }).catch((e) => ({ code: 1, stderr: String(e) }));
    this.sending = false;
    if (r.code === 0) {
      notify({ title: `Answered Q${number}`, body: `(${pick}) sent to Claude.`, level: "success", timeout: 3000 });
      this.open = null;
      cx.spawn((cx) => this.refresh(cx));
    } else {
      notify({ title: "Tempo", body: r.stderr.trim() || "The answer wasn't saved.", level: "error" });
    }
    cx.notify();
  }

  // The person's own follow-up: Claude answers it in the question.
  async followUp(cx) {
    const body = this.more.value().trim();
    const { project, number } = this.open ?? {};
    if (!body || !project || this.sending) return;
    this.sending = true;
    cx.notify();
    const args = ["reply", String(number), body, "--project", project];
    const r = await exec(this.tempo, args, { cwd: home() }).catch((e) => ({ code: 1, stderr: String(e) }));
    this.sending = false;
    if (r.code === 0) {
      this.more.set_value("");
      this.busy = false;
      await this.refresh(cx);
      await this.load(cx);
    } else {
      notify({ title: "Tempo", body: r.stderr.trim() || "The follow-up wasn't saved.", level: "error" });
    }
    cx.notify();
  }

  render(cx) {
    const t = theme();
    const cwd = shownFolder();
    if (this.tempo && cwd !== this.mine?.cwd && !this.busy) cx.spawn((cx) => this.refresh(cx));
    const body = this.open ? this.detail(t) : this.list(t, cwd);
    return v_flex()
      .id("root")
      .size_full()
      .bg(t.chrome)
      .text_size(13)
      .text_color(t.foreground)
      .child(this.header(t))
      .child(body);
  }

  header(t) {
    const mine = this.mine?.questions ?? [];
    const row = h_flex().h(40).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border);
    if (this.open) {
      return row
        .child(
          div()
            .id("tempo-back")
            .px(6)
            .py(2)
            .rounded(6)
            .text_color(t.muted)
            .cursor_pointer()
            .hover((s) => s.bg(t.hover).text_color(t.foreground))
            .on_click((_e, cx) => {
              this.open = null;
              cx.notify();
            })
            .child("‹ Questions"),
        )
        .child(div().flex_1())
        .child(div().text_color(t.dim).truncate().child(this.open.project));
    }
    return row
      .child(div().font_semibold().child("Questions"))
      .child(div().flex_1().truncate().text_color(t.muted).child(this.mine?.project ?? ""))
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
      );
  }

  list(t, cwd) {
    const mine = this.mine?.questions ?? [];
    const project = this.mine?.project;
    const others = groupBy(
      this.all.filter((q) => q.project !== project),
      (q) => q.project,
    );
    return v_flex()
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
      ]);
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
      .on_click((_e, cx) => cx.spawn((cx) => this.show(q.project, q.number, cx)))
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
      .on_click((_e, cx) => cx.spawn((cx) => this.show(name, qs[0].number, cx)))
      .child(div().flex_1().truncate().child(name))
      .child(div().text_color(t.muted).child(String(qs.length)));
  }

  // One question in full: what it is, the options to pick from, and its thread.
  detail(t) {
    const q = this.open.question;
    const pane = v_flex().id("tempo-detail").flex_1().min_h(0).overflow_y_scrollbar().p(12).gap(12);
    if (!q) return pane.child(div().text_color(t.muted).child("Reading…"));
    const pick = this.open.pick;
    return pane
      .child(
        v_flex()
          .gap(4)
          .child(
            h_flex()
              .gap(6)
              .text_size(11)
              .text_color(t.dim)
              .child(div().text_color(t.accent).child(q.label))
              .when(Boolean(q.goal), (el) => el.child(div().truncate().child(q.goal))),
          )
          .child(div().text_size(14).font_semibold().child(q.title)),
      )
      .when(Boolean(q.waiting), (el) =>
        el.child(
          div()
            .px(8)
            .py(6)
            .rounded(6)
            .bg(t.raised)
            .text_size(12)
            .text_color(t.working)
            .child("Waiting on Claude to answer your follow-up."),
        ),
      )
      .child(
        TextView.markdown(`ctx-${q.project}-${q.number}-${q.version}`, q.context ?? "")
          .selectable(true)
          .text_size(12.5)
          .text_color(t.foreground),
      )
      .child(
        v_flex()
          .gap(4)
          .children(q.options.map((o) => this.option(t, o, o.letter === pick, o.letter === q.recommended))),
      )
      .child(this.field(t, this.note))
      .child(
        h_flex()
          .id("tempo-send")
          .h(32)
          .justify_center()
          .items_center()
          .rounded(8)
          .font_medium()
          .text_size(12.5)
          .bg(pick ? t.solid : t.raised)
          .text_color(pick ? t.solid_foreground : t.dim)
          .when(Boolean(pick) && !this.sending, (el) =>
            el.cursor_pointer().hover((s) => s.opacity(0.88)),
          )
          .on_click((_e, cx) => cx.spawn((cx) => this.send(cx)))
          .child(this.sending ? "Sending…" : pick ? `Answer (${pick})` : "Pick an option"),
      )
      .child(heading(t, "Need more before deciding?"))
      .child(this.field(t, this.more))
      .when(q.entries.length > 0, (el) =>
        el.child(heading(t, "Thread")).children(q.entries.map((e) => this.entry(t, e))),
      );
  }

  option(t, o, picked, recommended) {
    return h_flex()
      .id(`opt-${o.letter}`)
      .gap(8)
      .px(8)
      .py(6)
      .rounded(8)
      .items_start()
      .border_1()
      .border_color(picked ? t.accent : t.border)
      .bg(picked ? t.selected : t.surface)
      .cursor_pointer()
      .hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => {
        this.open.pick = picked ? null : o.letter;
        cx.notify();
      })
      .child(div().flex_none().text_color(t.accent).child(`(${o.letter})`))
      .child(div().flex_1().min_w(0).child(o.text))
      .when(recommended, (el) =>
        el.child(div().flex_none().text_size(11).text_color(t.done).child("recommended")),
      );
  }

  field(t, state) {
    return Input.new(state)
      .h(32)
      .px(10)
      .rounded(8)
      .border_1()
      .border_color(t.input)
      .bg(t.background)
      .text_size(12.5)
      .text_color(t.foreground);
  }

  entry(t, e) {
    const who = e.author === "user" ? "You" : "Claude";
    const what =
      e.kind === "answer"
        ? `answered (${e.choice})${e.body ? `: ${e.body}` : ""}`
        : e.kind === "reword"
          ? `reworded the question${e.body ? `: ${e.body}` : ""}`
          : e.body;
    return v_flex()
      .gap(2)
      .px(8)
      .py(6)
      .rounded(6)
      .bg(t.surface)
      .text_size(12)
      .child(
        h_flex()
          .gap(6)
          .text_size(11)
          .text_color(t.dim)
          .child(who)
          .child(e.created_at.slice(0, 16).replace("T", " ")),
      )
      .child(div().child(what));
  }
}

// The folder of the shown tab's focused terminal pane, or of its first one; null for a
// space on another machine.
function shownFolder() {
  const space = spaces().find((s) => s.active);
  if (!space || space.machine) return null;
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

// Run tempo and read its JSON output.
async function tempo(path, args, cwd) {
  const r = await exec(path, args, { cwd, timeout: 15000 });
  if (r.code !== 0) throw new Error(r.stderr.trim() || `tempo ${args[0]} failed (${r.code})`);
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
  return div().px(2).pt(6).text_size(11).text_color(t.dim).truncate().child(text);
}
