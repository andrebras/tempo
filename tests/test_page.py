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
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock, skipUnless

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
        with mock.patch("tempo.store.now", return_value="2099-01-01T00:00:00Z"):
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
        self.run_js("checkServer()")
        self.wait("!window.beforeRestart && %s && $('#note')" % LOADED)
        self.assertEqual(self.run_js("$('#note').value"), "Right, if it's dry.")
        self.assertEqual(self.run_js("$('input[name=choice]:checked')?.value"), "b")
