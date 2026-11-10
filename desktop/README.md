# Tempo desktop app (early test)

The Tempo page, with its questions and its terminal, in a window of its own instead of a
browser tab. It is built with [Tauri](https://tauri.app) 2: a small Rust program that
opens a native window with the system's own web view (WebKit on macOS), so it ships no
browser of its own. So far it is built and run from a Tempo checkout, on a Mac.

## What it does

- On start, it runs a Tempo server of its own, `bin/tempo serve`, on a free port. The page
  your Claude sessions start on port 2204 is left alone.
- It waits until that server answers `/health`, then opens one window on its page.
- It makes a random token at each start and passes it to the server in `TEMPO_APP_TOKEN`.
  That server then opens shells only for requests that carry the token, and its page holds
  no secret, so a browser tab on the same port can show the questions but not use the
  terminal. The window gets the token from a script that Tauri runs before the page's own
  scripts (`window.tempoAppToken`), only while it shows that page. The token is never in a
  URL, so it never lands in a log or the browser history.
- Links to other sites open in your default browser, never in the window.
- On quit (closing the window, Cmd-Q, Ctrl-C or SIGTERM), it stops its server, which ends
  the shells it kept. Shells in tmux go on, as they do when the page server stops.

The terminal still has to be turned on, in the page's Settings or with
`bin/tempo config terminal on`.

## What you need

- Node.js and npm, for the Tauri command-line tool (`@tauri-apps/cli`, pinned in
  `package.json`).
- Rust 1.90 or newer, with `cargo` (for example from [rustup](https://rustup.rs) or asdf).
- On a Mac, the Xcode Command Line Tools (`xcode-select --install`).
- Python 3.9 or newer, as for Tempo itself.

## Run it

```sh
cd desktop
npm install
npm run tauri dev
```

The first run compiles Tauri and takes a few minutes; later runs take seconds. The app
runs `bin/tempo` from this checkout. Set `TEMPO_ROOT` to run another Tempo folder, and
`TEMPO_DB` to show another database, for example a scratch one while trying things out:

```sh
TEMPO_DB=/tmp/tempo-try.db npm run tauri dev
```

## Build it

```sh
npm run tauri build            # desktop/src-tauri/target/release/bundle/macos/Tempo.app
npm run tauri build -- --debug # the same, faster to build, in target/debug
```

The built app still runs `bin/tempo` from the checkout it was built in (or `TEMPO_ROOT`):
it does not carry Tempo inside it yet.

## Not done yet

- Linux: the code is meant to work there (it uses `xdg-open` for links), but it has not
  been built or tried.
- Signing and notarisation, so macOS opens a downloaded copy without warnings.
- Carrying Tempo inside the app, so it runs without a checkout.
- Notifications for new questions, an icon in the menu bar (tray), and starting at login.
- Tempo's own icon: the icons in `src-tauri/icons` are Tauri's defaults.
- Building the app in CI: `bin/ci` and the GitHub checks do not compile the Rust code.
