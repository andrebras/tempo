"""The page's main flows, run in headless Google Chrome against the real server code.

Each test gets a fresh database and serves the page on a new free port. A new port is a
new origin for the browser, so nothing the page keeps in localStorage or sessionStorage
carries over from one test to the next. One Chrome runs for the whole class and is driven
over the Chrome DevTools Protocol (CDP), the same way bin/media drives it for the README's
pictures.

The tests skip when Google Chrome isn't installed. Set CHROME to the browser's path if it
isn't found on its own.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase, mock, skipUnless

from tempo import terminal
from tempo.server import make_handler
from tests.helpers import DatabaseTest

ROOT = Path(__file__).resolve().parent.parent


def load_media():
    """bin/media as a module, for its headless Chrome driver. It has no .py ending."""
    path = str(ROOT / "bin" / "media")
    loader = importlib.machinery.SourceFileLoader("bin_media", path)
    spec = importlib.util.spec_from_loader("bin_media", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


media = load_media()
CHROME = media.find_chrome()
# The page has drawn its list and a question.
LOADED = "document.querySelector('#detail h2') && document.querySelector('#list a')"


@skipUnless(CHROME, "Google Chrome is not installed (set CHROME to its path to run these)")
class PageTest(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory(prefix="tempo-page-tests-")
        cls.browser = media.Browser(CHROME, Path(cls.profile.name)).__enter__()
        # The page's four-second timer is switched off, so it can't step in mid-test and
        # make a broken flow pass anyway. Tests run its work themselves (see tick).
        cls.browser.send(
            "Page.addScriptToEvaluateOnNewDocument", source="window.setInterval = () => 0;"
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.__exit__()
        cls.profile.cleanup()

    def setUp(self):
        super().setUp()
        self.port = media.free_port()
        self.url = "http://127.0.0.1:%d/" % self.port
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), None)
        self.server.RequestHandlerClass = self.handler()
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.add(title="Which way?", options=["Left", "Right"])
        self.add(title="How fast?", options=["Slow", "Fast"])

    def handler(self):
        # The server asks git for its commit; the test's own git settings stay out of it.
        clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        with mock.patch.dict(os.environ, clean, clear=True):
            return make_handler(self.port)

    # Driving the page

    def run_js(self, script):
        result = self.browser.send(
            "Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True
        )
        if "exceptionDetails" in result:
            raise AssertionError("the page failed on %s: %s" % (script, result["exceptionDetails"]))
        return result["result"].get("value")

    def wait(self, condition, timeout=5):
        """Wait until the condition holds on the page; false while it is still loading."""
        end = time.monotonic() + timeout
        while True:
            try:
                if self.run_js("Boolean(%s)" % condition):
                    return
            except AssertionError:
                pass
            if time.monotonic() > end:
                self.fail("the page never showed %s" % condition)
            time.sleep(0.02)

    def open(self, route, ready=LOADED):
        self.browser.send("Page.navigate", url=self.url + route)
        self.wait(ready)

    def click(self, selector):
        """A real mouse click in the middle of the element, as a user makes it."""
        box = self.run_js(
            "(() => { const e = document.querySelector(%s); e.scrollIntoView({block: 'center'});"
            " const r = e.getBoundingClientRect();"
            " return [r.left + r.width / 2, r.top + r.height / 2]; })()" % json.dumps(selector)
        )
        for kind in ("mousePressed", "mouseReleased"):
            self.browser.send(
                "Input.dispatchMouseEvent",
                type=kind,
                x=box[0],
                y=box[1],
                button="left",
                clickCount=1,
            )

    def type(self, text):
        self.browser.send("Input.insertText", text=text)

    def press_escape(self):
        for kind in ("keyDown", "keyUp"):
            self.browser.send(
                "Input.dispatchKeyEvent",
                type=kind,
                key="Escape",
                code="Escape",
                windowsVirtualKeyCode=27,
            )

    def focused(self):
        """The focused control's accessible name: its label, else its text."""
        return self.run_js(
            "(e => e.getAttribute('aria-label') || e.textContent)(document.activeElement)"
        )

    def text(self, selector):
        return self.run_js("document.querySelector(%s)?.textContent" % json.dumps(selector))

    def tick(self):
        """What the page does every four seconds, now, so no test waits for the timer."""
        self.run_js("refresh(false)")

    # The flows

    def test_picks_an_option_and_sends_it(self):
        self.open("#/demo/1")
        self.assertEqual(self.text("#detail h2"), "Which way?")
        self.assertTrue(self.run_js("$('#send').disabled"), "nothing is picked yet")
        self.click("#detail input[value=b] + .letter")
        self.wait("!$('#send').disabled")
        self.click("#send")
        # The page moves on to the next open question.
        self.wait("location.hash === '#/demo/2' && $('#detail h2').textContent === 'How fast?'")
        question = self.store.get("demo", 1)
        self.assertEqual(question["status"], "answered")
        self.assertEqual(question["entries"][-1]["choice"], "b")

    def test_asks_for_more_context(self):
        self.open("#/demo/1")
        self.click("#note")
        self.type("What does each way cost?")
        self.click("#detail .actions button:not(.primary)")
        self.wait("$('#detail .eyebrow .wait')")
        entry = self.store.get("demo", 1)["entries"][-1]
        self.assertEqual((entry["kind"], entry["body"]), ("followup", "What does each way cost?"))
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_shows_new_projects_and_changed_questions(self):
        self.open("#/demo/1")
        # The first visit counts everything as seen, so nothing is new yet.
        self.assertFalse(self.run_js("$('#chips .dot') || $('#list .dot')"))
        self.add(project="garden", title="Frost warnings on?")
        # Times are kept to the second, so the context is dated a little later.
        with mock.patch("tempo.store.common.now", return_value="2099-01-01T00:00:00Z"):
            self.store.add_context("demo", 2, "Fast costs twice as much.")
        self.tick()
        self.wait(
            "[...document.querySelectorAll('#chips .chip')].some((chip) =>"
            " chip.textContent.startsWith('garden') && chip.querySelector('.dot'))"
        )
        self.wait("$('#list a[href=\"#/demo/2\"] .dot')")
        self.assertFalse(self.run_js("$('#list a[href=\"#/demo/1\"] .dot')"), "Q1 is on screen")

    def test_a_link_to_a_missing_question_says_so(self):
        self.open("#/demo/99", ready="$('#detail h2') && $('#list a')")
        self.wait("$('#detail h2').textContent === 'Question not found'")
        # The timed refresh keeps saying so instead of opening another question.
        self.tick()
        self.assertEqual(self.text("#detail h2"), "Question not found")
        self.assertEqual(self.run_js("location.hash"), "#/demo/99")

    def test_a_reword_keeps_the_note_and_drops_the_pick(self):
        self.open("#/demo/1")
        self.click("#detail input[value=b] + .letter")
        self.click("#note")
        self.type("Left is closer.")
        self.store.reword("demo", 1, "Named the places.", title="Which way to the station?")
        self.tick()
        self.wait("$('#detail h2').textContent === 'Which way to the station?'")
        self.assertEqual(self.run_js("$('#note').value"), "Left is closer.")
        # After a reword the same letter may name another option, so the pick is dropped.
        self.assertFalse(self.run_js("$('input[name=choice]:checked')"))
        self.assertTrue(self.run_js("$('#send').disabled"))

    def test_an_answer_to_an_older_wording_is_refused_and_shows_the_new_one(self):
        self.open("#/demo/1")
        self.click("#detail input[value=a] + .letter")
        self.click("#note")
        self.type("Left is closer.")
        # Claude swaps the options before the timed refresh redraws them.
        self.store.reword("demo", 1, "Swapped", options=["Right", "Left"])
        self.click("#send")
        self.wait("$('#detail .changed')?.textContent.includes('reworded this question')")
        self.assertEqual(self.store.get("demo", 1)["status"], "open")
        self.assertEqual(self.text("#detail .options label .text"), "Right")
        self.assertEqual(self.run_js("$('#note').value"), "Left is closer.")
        self.assertFalse(self.run_js("$('input[name=choice]:checked')"))

    def test_a_question_answered_elsewhere_keeps_the_note_on_screen(self):
        self.open("#/demo/1")
        self.click("#note")
        self.type("half-written reasoning")
        self.store.answer("demo", 1, "a")
        self.tick()
        self.wait("$('#detail .changed')")
        self.assertIn("Q1 was answered elsewhere", self.text("#detail .changed"))
        self.assertEqual(self.run_js("location.hash"), "#/demo/1")
        self.assertEqual(self.run_js("$('#note').value"), "half-written reasoning")
        # It stays put on later refreshes, until the user moves on.
        self.tick()
        self.assertEqual(self.run_js("document.querySelectorAll('#detail .changed').length"), 1)
        self.click("#detail .changed button")
        self.wait("$('#detail h2')?.textContent === 'How fast?'")

    def test_a_question_answered_elsewhere_without_a_draft_gives_way_to_the_next(self):
        self.open("#/demo/1")
        self.store.answer("demo", 1, "a")
        self.tick()
        self.wait("$('#detail h2')?.textContent === 'How fast?'")

    def test_reopens_an_answered_question(self):
        self.store.answer("demo", 1, "a")
        self.open("#/demo/1")
        self.wait("$('#detail .actions button')?.textContent === 'Reopen to answer again'")
        self.click("#detail .actions button")
        self.wait("$('#send')?.textContent === 'Send new answer'")
        self.assertIn("Your previous answer", self.text("#detail .answered"))
        self.assertTrue(self.run_js("state.status === 'open'"), "the page moved to the Open tab")
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_the_timed_refresh_keeps_focus(self):
        self.open("#/demo/1")
        self.click("#note")
        self.type("Left, unless")
        self.run_js("$('#note').setSelectionRange(4, 4)")
        # New context changes the question, so the refresh draws it again.
        self.store.add_context("demo", 1, "The left road is closed on Sundays.")
        self.tick()
        self.wait("$('#detail .thread').textContent.includes('closed on Sundays')")
        self.assertTrue(self.run_js("document.activeElement === $('#note')"))
        self.assertEqual(
            self.run_js("[$('#note').selectionStart, $('#note').value]"), [4, "Left, unless"]
        )
        # A list link keeps the keyboard too when the list changes under it.
        self.run_js("$('#list a[href=\"#/demo/2\"]').focus()")
        self.add(title="How far?")
        self.tick()
        self.wait("$('#list a[href=\"#/demo/3\"]')")
        self.assertEqual(self.run_js("document.activeElement.getAttribute('href')"), "#/demo/2")

    def test_a_server_restart_reloads_the_page_and_keeps_the_draft(self):
        self.open("#/demo/1")
        self.wait("firstServer")
        self.click("#detail input[value=b] + .letter")
        self.click("#note")
        self.type("Right, if it's dry.")
        # A restarted server reports another start time on the same address.
        with mock.patch("tempo.server._STARTED", time.gmtime(0)):
            self.server.RequestHandlerClass = self.handler()
        self.run_js("window.beforeRestart = true")
        # Not awaited: the page reloads before the call would return.
        self.run_js("setTimeout(checkServer, 0)")
        self.wait("!window.beforeRestart && %s && $('#note')" % LOADED)
        self.assertEqual(self.run_js("$('#note').value"), "Right, if it's dry.")
        self.assertEqual(self.run_js("$('input[name=choice]:checked')?.value"), "b")

    def test_long_words_wrap_instead_of_widening_the_page(self):
        long = "/very/long/" + "x" * 216
        self.add(title="Use " + long + "?", options=["Keep " + long, "Drop"], goal="Goal " + long)
        self.open("#/demo/3")
        self.wait("$('#list h3.goal')")
        for pane in ("#detail", "#list"):
            element = "$(%s)" % json.dumps(pane)
            extra = self.run_js("%s.scrollWidth - %s.clientWidth" % (element, element))
            self.assertLessEqual(extra, 0, "%s scrolls sideways" % pane)

    def test_the_question_screen_names_its_controls(self):
        self.open("#/demo/1")
        self.assertEqual(self.run_js("$('#note').getAttribute('aria-label')"), "Your note")
        options = self.run_js("$('[role=radiogroup]').getAttribute('aria-label')")
        self.assertEqual(options, "Which way?")
        current = "[...document.querySelectorAll('#list a[aria-current=page]')].map((a) => a.hash)"
        self.assertEqual(self.run_js(current), ["#/demo/1"])

    def test_a_broken_address_does_not_stop_the_refresh(self):
        # A bad %-escape used to make every refresh throw, so the page stopped updating.
        self.open("#/%E0/1")
        self.add(title="How far?")
        self.tick()
        self.wait("$('#list a[href=\"#/demo/3\"]')")

    def test_saving_in_settings_keeps_the_keyboard_on_the_button(self):
        self.open("#/settings", ready="$('#settings .segmented button')")
        off = "[aria-label='Start the page with each session'] button:last-child"
        self.click(off)
        self.wait("$(%s).getAttribute('aria-pressed') === 'true'" % json.dumps("#settings " + off))
        self.assertFalse(self.store.start_page())
        self.assertEqual(self.run_js("document.activeElement.textContent"), "Off")
        self.assertTrue(self.run_js("$('#settings').contains(document.activeElement)"))

    # The terminal under the question

    def start_terminal(self):
        """Turn the terminal on for demo, in a throwaway folder, with a plain /bin/sh."""
        folder = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, folder)
        self.store.see_folder("demo", folder)
        self.store.set_terminal(True)
        shell = mock.patch.dict(os.environ, {"SHELL": "/bin/sh", "PS1": "$ "})
        shell.start()
        self.addCleanup(shell.stop)
        # The server keeps the shells itself, so no tmux session is started.
        tmux = mock.patch.object(terminal, "TMUX", None)
        tmux.start()
        self.addCleanup(tmux.stop)
        self.addCleanup(terminal.SHELLS.close_all)
        return folder

    def press_enter(self):
        for kind in ("keyDown", "keyUp"):
            self.browser.send(
                "Input.dispatchKeyEvent",
                type=kind,
                key="Enter",
                code="Enter",
                windowsVirtualKeyCode=13,
                text="\r" if kind == "keyDown" else "",
            )

    def test_the_terminal_runs_a_command_in_the_projects_folder(self):
        folder = self.start_terminal()
        self.open("#/demo/1", ready=LOADED + " && !$('#terminal-toggle').hidden")
        self.click("#terminal-toggle")
        self.wait("$('#terminal .where').textContent === %s" % json.dumps(folder))
        self.assertEqual(self.text("#terminal .name"), "demo")
        self.wait("document.activeElement.classList.contains('xterm-helper-textarea')")
        self.type("echo hi$((1+1))")
        self.press_enter()
        # The sum shows the shell ran the line, not only echoed what was typed.
        self.wait("$('#terminal .xterm-rows').textContent.includes('hi2')")
        [key] = terminal.SHELLS.shells
        shell = terminal.SHELLS.get(key)
        self.click("#terminal .end")
        self.wait("$('#terminal').hidden && !$('#terminal .term')")
        self.assertEqual(shell.process.wait(timeout=5), shell.process.returncode)
        self.assertEqual(terminal.SHELLS.shells, {})

    def test_a_hidden_terminal_lets_go_of_its_connection_and_comes_back_as_it_was(self):
        self.start_terminal()
        garden = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, garden)
        self.add(project="garden", title="Frost warnings on?")
        self.store.see_folder("garden", garden)
        self.open("#/demo/1", ready=LOADED + " && !$('#terminal-toggle').hidden")
        self.click("#terminal-toggle")
        self.wait("$('#terminal .mode').textContent === 'kept alive by the page server'")
        self.wait("document.activeElement.classList.contains('xterm-helper-textarea')")
        self.type('KEPT=ab; echo re""ady')
        self.press_enter()
        rows = "terminals.demo.holder.querySelector('.xterm-rows').textContent"
        self.wait("%s.includes('ready')" % rows)
        [key] = terminal.SHELLS.shells
        self.run_js("location.hash = '#/garden/1'")
        self.wait("$('#terminal .name').textContent === 'garden' && terminals.garden.id")
        # Only the terminal shown reads its output; demo's shell runs on without a reader.
        self.assertFalse(self.run_js("Boolean(terminals.demo.reading)"))
        self.assertIsNone(terminal.SHELLS.get(key).process.poll())
        self.run_js("location.hash = '#/demo/1'")
        self.wait("$('#terminal .name').textContent === 'demo' && terminals.demo.reading")
        self.assertFalse(self.run_js("Boolean(terminals.garden.reading)"))
        self.wait("terminals.demo.holder.contains(document.activeElement)")
        self.type('echo "$KEPT"cd')
        self.press_enter()
        self.wait("%s.includes('abcd')" % rows)
        # The output from before is still there, once: coming back asked only for the rest.
        self.assertEqual(self.run_js("%s.split('ready').length - 1" % rows), 1)
        self.click("#terminal .hide")
        self.wait("$('#terminal').hidden && !terminals.demo.reading")

    def test_the_terminal_takes_the_pages_colours_and_font(self):
        self.start_terminal()
        self.store.set_terminal_size(17)
        self.open("#/demo/1", ready=LOADED + " && !$('#terminal-toggle').hidden")
        self.click("#terminal-toggle")
        self.wait("$('#terminal .xterm-rows')")
        panel = "getComputedStyle(document.documentElement).getPropertyValue('--panel').trim()"
        theme = "terminals.demo.term.options.theme.background"
        self.assertEqual(self.run_js(theme), self.run_js(panel))
        self.assertEqual(self.run_js("terminals.demo.term.options.fontSize"), 17)
        self.assertIn("ui-monospace", self.run_js("terminals.demo.term.options.fontFamily"))
        self.run_js("setDisplay('theme', 'dark', false)")
        self.assertEqual(self.run_js(theme), self.run_js(panel))
        self.assertEqual(self.run_js(theme), "#1d1c24")

    def test_without_the_setting_there_is_no_terminal(self):
        self.open("#/demo/1")
        self.assertTrue(self.run_js("$('#terminal-toggle').hidden"))
        self.assertFalse(self.run_js("typeof Terminal !== 'undefined'"))

    def test_turns_the_terminal_on_in_settings(self):
        self.open("#/settings", ready="$('#settings .segmented button')")
        on = '[aria-label="Terminal in the project\'s folder"] button:first-child'
        self.click("#settings " + on)
        self.wait("!$('#terminal-toggle').hidden")
        self.assertTrue(self.store.terminal())

    def test_turning_the_terminal_off_in_settings_first_says_what_it_ends(self):
        terminal.SHELLS.open(self.start_terminal())
        self.open("#/settings", ready="$('#settings .segmented button')")
        off = '#settings [aria-label="Terminal in the project\'s folder"] button:last-child'
        self.click(off)
        self.wait("$('#settings .confirm:not([hidden])')")
        self.assertEqual(
            self.text("#settings .confirm p"),
            "Turning the terminal off ends 1 running terminal; anything running in it stops.",
        )
        self.assertEqual(self.focused(), "Turn off")
        self.press_escape()
        self.wait("$('#settings .confirm').hidden")
        self.assertTrue(self.store.terminal())
        self.assertEqual(len(terminal.SHELLS.shells), 1)
        self.click(off)
        self.wait("$('#settings .confirm:not([hidden])')")
        self.click("#settings .confirm button.primary")
        notice = "$('#settings [role=status]')?.textContent"
        self.wait("%s === 'Terminal is off. Ended 1 terminal.'" % notice)
        self.assertFalse(self.store.terminal())
        self.assertEqual(terminal.SHELLS.shells, {})

    def test_merges_one_project_into_another_from_settings(self):
        # A folder renamed by hand: its old project keeps its question until it is merged.
        self.add(project="old-name", title="Which colour?")
        pick = "[aria-label='Merge old-name into']"
        self.open("#/settings", ready="$(%s)" % json.dumps(pick))
        self.click("[aria-label='Edit old-name']")
        self.wait("$(%s).checkVisibility()" % json.dumps(pick))
        self.run_js(
            "(s => { s.value = 'demo'; s.dispatchEvent(new Event('change', {bubbles: true})); })"
            "($(%s))" % json.dumps(pick)
        )
        self.click("form:has(%s) button" % pick)
        # Nothing moves until the inline confirmation is accepted; the keyboard is on it.
        self.wait("$('#settings .confirm:not([hidden])')")
        self.assertEqual(self.run_js("document.activeElement.textContent"), "Merge into demo")
        self.assertIn("old-name", [project["name"] for project in self.store.project_summaries()])
        self.click("#settings .confirm .primary")
        self.wait("$('#settings p[role=status]')")
        self.assertEqual(
            self.text("#settings p[role=status]"), "Moved 1 question from old-name to demo: now Q3."
        )
        self.assertEqual(
            [(p["name"], p["questions"]) for p in self.store.project_summaries()], [("demo", 3)]
        )
        self.assertIsNone(self.run_js("$(%s)" % json.dumps(pick)))
        # The panel closed, and the keyboard is on the project the questions went to.
        self.assertEqual(self.focused(), "Edit demo")

    def test_edit_opens_one_project_at_a_time_and_renames_it(self):
        self.add(project="garden", title="Frost warnings on?")
        self.open("#/settings", ready="$(\"[aria-label='Edit garden']\")")
        # Only names, counts, folders and Edit buttons show until one is opened.
        self.assertFalse(self.run_js("$('#settings .editor input').checkVisibility()"))
        self.click("[aria-label='Edit demo']")
        self.click("[aria-label='Edit garden']")
        self.wait("document.activeElement.getAttribute('aria-label') === 'New name for garden'")
        self.assertEqual(
            self.run_js("document.querySelectorAll('#settings .editor:not([hidden])').length"), 1
        )
        self.assertEqual(
            self.run_js("$(\"[aria-label='Edit garden']\").getAttribute('aria-expanded')"), "true"
        )
        # The name is selected, so typing replaces it.
        self.type("Garden")
        self.click("#settings .editor:not([hidden]) form button")
        self.wait("$(\"[aria-label='Edit Garden']\")")
        self.assertEqual(self.store.get("Garden", 1)["title"], "Frost warnings on?")
        self.assertEqual(self.focused(), "Edit Garden")
        self.assertEqual(
            self.run_js("document.querySelectorAll('#settings .editor:not([hidden])').length"), 0
        )

    def test_escape_cancels_the_merge_then_closes_the_editor(self):
        self.add(project="old-name", title="Which colour?")
        pick = "[aria-label='Merge old-name into']"
        self.open("#/settings", ready="$(\"[aria-label='Edit old-name']\")")
        self.click("[aria-label='Edit old-name']")
        self.wait("document.activeElement.getAttribute('aria-label') === 'New name for old-name'")
        self.type("typed")
        self.run_js(
            "(s => { s.value = 'demo'; s.dispatchEvent(new Event('change', {bubbles: true})); })"
            "($(%s))" % json.dumps(pick)
        )
        self.click("form:has(%s) button" % pick)
        self.wait("$('#settings .confirm:not([hidden])')")
        self.press_escape()
        self.wait("$('#settings .confirm').hidden")
        self.assertEqual(self.focused(), "Merge old-name into")
        self.press_escape()
        self.wait("$('#settings .editor:not([hidden])') === null")
        self.assertEqual(self.focused(), "Edit old-name")
        # Closing drops what was typed and picked.
        self.assertEqual(
            self.run_js("$(\"[aria-label='New name for old-name']\").value"), "old-name"
        )
        self.assertEqual(self.run_js("$(%s).value" % json.dumps(pick)), "")
        self.assertIn("old-name", [project["name"] for project in self.store.project_summaries()])


class ContrastTest(TestCase):
    """Text colours on the page meet WCAG's 4.5:1 minimum for normal text, in both themes.

    WCAG is the Web Content Accessibility Guidelines; the ratio compares the relative
    luminance of the text and its background.
    """

    # (text, background) colour tokens that the page puts together.
    PAIRS = (
        ("text", "bg"),
        ("muted", "bg"),
        ("muted", "panel"),
        ("muted", "sunken"),
        ("later", "later-soft"),
        ("accent", "panel"),
        ("accent", "sunken"),
        ("accent", "accent-soft"),
        ("on-accent", "accent"),
        ("on-accent", "accent-strong"),
        ("done", "done-soft"),
        ("now", "panel"),
    )

    @staticmethod
    def luminance(colour):
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def themes(self):
        html = (ROOT / "tempo" / "page.html").read_text()
        light = re.search(r":root \{\s*color-scheme: light;(.*?)\}", html, re.S).group(1)
        dark = re.findall(r"color-scheme: dark;(.*?)\}", html, re.S)
        self.assertEqual(len(dark), 2, "the system and forced dark blocks")
        self.assertEqual(dark[0].split(), dark[1].split(), "the two dark blocks must match")
        for name, block in (("light", light), ("dark", dark[0])):
            yield name, dict(re.findall(r"--([\w-]+): (#[0-9a-f]{6});", block))

    def test_text_colours_meet_the_minimum(self):
        for theme, tokens in self.themes():
            for text, background in self.PAIRS:
                lighter, darker = sorted(
                    (self.luminance(tokens[text]), self.luminance(tokens[background])), reverse=True
                )
                ratio = (lighter + 0.05) / (darker + 0.05)
                with self.subTest(theme=theme, text=text, background=background):
                    self.assertGreaterEqual(round(ratio, 2), 4.5)
