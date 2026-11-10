//! Tempo's desktop app: the Tempo page in a native window.
//!
//! On start it runs a Tempo server of its own (`bin/tempo serve`) on a free port, so it never
//! takes the port of the page the Claude sessions start (2204). It hands that server a random
//! token in TEMPO_APP_TOKEN and gives the token to its window only, so only this window can
//! open shells in the page's terminal. On quit it stops the server.

use std::fs::File;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::{Mutex, OnceLock};
use std::thread::sleep;
use std::time::{Duration, Instant};

use tauri::webview::NewWindowResponse;
use tauri::{Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};

/// The page server this app started, until it is stopped.
struct Server(Mutex<Option<Child>>);

type Failure = Box<dyn std::error::Error>;

/// The signals that quit this app, which every thread holds back (see hold_quit_signals).
static QUIT_SIGNALS: OnceLock<libc::sigset_t> = OnceLock::new();

fn main() {
    hold_quit_signals();
    tauri::Builder::default()
        .setup(|app| {
            quit_on_signals(app.handle().clone());
            let port = free_port()?;
            let token = new_token()?;
            let mut server = start_server(port, &token)?;
            if let Err(error) = wait_until_up(port, &mut server) {
                stop(server);
                return Err(error);
            }
            app.manage(Server(Mutex::new(Some(server))));
            if let Err(error) = open_window(app, port, &token) {
                stop_server(app.handle());
                return Err(error);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tempo's window could not start")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_server(app);
            }
        });
}

/// Hold back Ctrl-C (SIGINT), SIGTERM and SIGHUP from every thread, before any other thread
/// starts, so that quit_on_signals receives them. Their default would end this app at once,
/// leaving its server running.
fn hold_quit_signals() {
    // SAFETY: the set is plain data that sigemptyset fills before use, and the calls only
    // change this thread's signal mask.
    unsafe {
        let mut signals: libc::sigset_t = std::mem::zeroed();
        libc::sigemptyset(&mut signals);
        for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
            libc::sigaddset(&mut signals, signal);
        }
        libc::pthread_sigmask(libc::SIG_BLOCK, &signals, std::ptr::null_mut());
        QUIT_SIGNALS.get_or_init(|| signals);
    }
}

/// Quit the app the usual way, which stops its server, when one of the signals arrives.
fn quit_on_signals(app: tauri::AppHandle) {
    let Some(signals) = QUIT_SIGNALS.get().copied() else {
        return;
    };
    std::thread::spawn(move || {
        let mut signal = 0;
        // SAFETY: sigwait only reads the set and writes the signal number.
        if unsafe { libc::sigwait(&signals, &mut signal) } == 0 {
            app.exit(0);
        }
    });
}

/// Stop the server this app started, if it still runs.
fn stop_server(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<Server>() else {
        return;
    };
    let held = state.0.lock().ok().and_then(|mut held| held.take());
    if let Some(server) = held {
        stop(server);
    }
}

/// The Tempo checkout or plugin folder to run: TEMPO_ROOT if set, else the checkout this app
/// was built from (desktop/src-tauri is two folders down from its root).
fn tempo_root() -> PathBuf {
    match std::env::var_os("TEMPO_ROOT") {
        Some(root) if !root.is_empty() => PathBuf::from(root),
        _ => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."),
    }
}

/// A port nothing listens on now: the system picks one, and it is let go at once for the
/// server to take.
fn free_port() -> Result<u16, Failure> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

/// 32 random bytes from the system, in hex, so the token is safe in a header and in a script.
fn new_token() -> Result<String, Failure> {
    let mut bytes = [0u8; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

/// `bin/tempo serve` in the foreground, as this app's child, with the rest of this app's
/// environment (so TEMPO_DB, if set, picks the database).
fn start_server(port: u16, token: &str) -> Result<Child, Failure> {
    let tempo = tempo_root().join("bin/tempo");
    if !tempo.is_file() {
        return Err(format!(
            "No Tempo at {}; set TEMPO_ROOT to a Tempo folder",
            tempo.display()
        )
        .into());
    }
    let server = child(tempo)
        .args(["serve", "--port", &port.to_string()])
        .env("TEMPO_APP_TOKEN", token)
        .spawn()?;
    Ok(server)
}

/// Wait, for up to 15 seconds, until the server answers /health as Tempo.
fn wait_until_up(port: u16, server: &mut Child) -> Result<(), Failure> {
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if let Some(status) = server.try_wait()? {
            return Err(format!("The Tempo server stopped at start ({status})").into());
        }
        if is_tempo(port) {
            return Ok(());
        }
        sleep(Duration::from_millis(100));
    }
    Err(format!("The Tempo server did not answer on port {port}").into())
}

/// Whether Tempo answers /health on the port. The server only answers requests that name
/// it as their Host.
fn is_tempo(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /health HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n");
    let mut answer = String::new();
    stream.write_all(request.as_bytes()).is_ok()
        && stream.read_to_string(&mut answer).is_ok()
        && answer.starts_with("HTTP/1.0 200")
        && answer.contains("\"app\": \"tempo\"")
}

/// The window on the server's page. A script that runs before the page's own gives it the
/// token, only while the window shows that page. Links to anywhere else open in the
/// default browser instead, so the window never shows another site.
fn open_window(app: &tauri::App, port: u16, token: &str) -> Result<(), Failure> {
    let origin = format!("http://127.0.0.1:{port}");
    let script = format!(
        "if (location.origin === {origin:?}) \
         Object.defineProperty(window, \"tempoAppToken\", {{ value: {token:?} }});"
    );
    let page: Url = format!("{origin}/").parse()?;
    let home = page.clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(page))
        .title("Tempo")
        .inner_size(1200.0, 800.0)
        .initialization_script(&script)
        .on_navigation(move |url| {
            let ours = url.origin() == home.origin();
            if !ours {
                open_in_browser(url);
            }
            ours
        })
        // A link that opens a new window, as the links in a question do.
        .on_new_window(|url, _| {
            open_in_browser(&url);
            NewWindowResponse::Deny
        })
        .build()?;
    Ok(())
}

/// A web or mail link in the default browser or mail app; anything else is ignored.
fn open_in_browser(url: &Url) {
    if !matches!(url.scheme(), "http" | "https" | "mailto") {
        return;
    }
    let opener = if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    let _ = child(opener).arg(url.as_str()).spawn();
}

/// Stop the server with SIGTERM, which lets it end its shells, and kill it if it is still
/// there after five seconds.
fn stop(mut server: Child) {
    // The id came from spawning this child, which has not been waited for yet, so it is
    // still this process and not a reused one.
    let pid = server.id() as libc::pid_t;
    unsafe {
        libc::kill(pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if matches!(server.try_wait(), Ok(Some(_))) {
            return;
        }
        sleep(Duration::from_millis(50));
    }
    let _ = server.kill();
    let _ = server.wait();
}

/// A command whose process starts without the signals this app holds back, which it would
/// otherwise inherit: the server would never see the SIGTERM that stop() sends.
fn child(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut command = Command::new(program);
    if let Some(signals) = QUIT_SIGNALS.get().copied() {
        // SAFETY: between fork and exec this only calls pthread_sigmask, which is safe there.
        unsafe {
            command.pre_exec(move || {
                libc::pthread_sigmask(libc::SIG_UNBLOCK, &signals, std::ptr::null_mut());
                Ok(())
            });
        }
    }
    command
}
