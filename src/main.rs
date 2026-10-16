mod input;
mod layout;
mod terminal;
mod tmux;
mod watch;

use std::env;
use std::ffi::OsString;
use std::io;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, ExitCode, Stdio};

fn run(args: &[OsString]) -> io::Result<()> {
    match args {
        [command, root] if command == "load" => tmux::load(Path::new(root)),
        [command, socket, session, pid, client] if command == "watch" => {
            watch::run(socket, session, pid, client)
        }
        [command, socket, client, session] if command == "popup" => {
            let tmux = tmux::Tmux::at(socket);
            let outcome = terminal::read_popup()?;
            // The client may have switched sessions while the popup was open.
            if !tmux.in_session(client, session) {
                return Ok(());
            }
            let status = Command::new(env::current_exe()?)
                .arg("replay")
                .args(tmux::replay_args(socket, client, outcome))
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()?;
            if status.success() {
                Ok(())
            } else {
                Err(io::Error::other("tmux replay failed"))
            }
        }
        [command, rest @ ..] if command == "replay" && !rest.is_empty() => {
            // Run in a fresh process so setsid needs no unsafe pre_exec hook.
            // tmux closing the popup must not interrupt close-and-replay.
            nix::unistd::setsid()?;
            Err(Command::new("tmux").args(rest).exec())
        }
        [command] if command == "--version" => {
            println!("tmux-prefix-help {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        [command] if command == "--help" || command == "-h" => {
            println!(
                "tmux-prefix-help\n\nLoad via prefix-help.tmux after building with cargo build --release --locked.\nInternal commands: load PLUGIN_DIR, watch SOCKET SESSION PID CLIENT, popup SOCKET CLIENT SESSION"
            );
            Ok(())
        }
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid arguments; use --help",
        )),
    }
}

fn main() -> ExitCode {
    match run(&env::args_os().skip(1).collect::<Vec<_>>()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("tmux-prefix-help: {error}");
            ExitCode::FAILURE
        }
    }
}
