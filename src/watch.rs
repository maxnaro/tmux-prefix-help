use crate::tmux::{self, Tmux};
use std::env;
use std::ffi::OsStr;
use std::fs::{self, File, OpenOptions, TryLockError};
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

fn cache_dir() -> io::Result<PathBuf> {
    let base = env::var_os("XDG_CACHE_HOME")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("HOME")
                .filter(|path| !path.is_empty())
                .map(|home| PathBuf::from(home).join(".cache"))
        })
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "HOME or XDG_CACHE_HOME is required",
            )
        })?;
    Ok(base.join("tmux-prefix-help"))
}

fn lock_client(directory: &Path, pid: u32, client: &OsStr) -> io::Result<Option<File>> {
    fs::create_dir_all(directory)?;
    // Hex preserves client identity; replacing punctuation could cause collisions.
    let identity: String = client
        .as_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .mode(0o600)
        .open(directory.join(format!("{pid}-{identity}.lock")))?;
    match lock.try_lock() {
        Ok(()) => Ok(Some(lock)),
        Err(TryLockError::WouldBlock) => Ok(None),
        Err(TryLockError::Error(error)) => Err(error),
    }
}

fn delay(value: &str) -> Duration {
    let millis = if !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()) {
        value.parse().unwrap_or(500)
    } else {
        500
    };
    Duration::from_millis(millis)
}

fn dimensions(value: &str) -> Option<(u32, u32)> {
    let mut parts = value.split_whitespace();
    let width = parts.next()?.parse::<u32>().ok()?;
    let height = parts.next()?.parse::<u32>().ok()?;
    if parts.next().is_some() || width < 3 || height < 6 {
        return None;
    }
    Some((width.min(70), height.min(16)))
}

pub fn run(socket: &OsStr, session: &OsStr, pid: &OsStr, client: &OsStr) -> io::Result<()> {
    let pid = pid
        .to_str()
        .and_then(|pid| pid.parse::<u32>().ok())
        .filter(|pid| *pid != 0)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid server PID"))?;
    let Some(_lock) = lock_client(&cache_dir()?, pid, client)? else {
        return Ok(());
    };
    let tmux = Tmux::at(socket);
    if !tmux.waiting(client, session) {
        return Ok(());
    }
    let configured = tmux::output(
        tmux.command()
            .args(["show-options", "-Av", "-t"])
            .arg(session)
            .arg("@prefix-help-delay"),
    )
    .unwrap_or_default();
    let mut remaining = delay(&configured);
    while !remaining.is_zero() {
        let interval = remaining.min(Duration::from_millis(50));
        thread::sleep(interval);
        if !tmux.waiting(client, session) {
            return Ok(());
        }
        remaining -= interval;
    }
    let Some((width, height)) = tmux
        .display(client, "#{client_width} #{client_height}")
        .ok()
        .and_then(|value| dimensions(&value))
    else {
        return Ok(());
    };
    let command = format!(
        "{} popup {} {} {}",
        tmux::shell_quote(env::current_exe()?.as_os_str())?,
        tmux::shell_quote(socket)?,
        tmux::shell_quote(client)?,
        tmux::shell_quote(session)?
    );
    if !tmux.waiting(client, session) {
        return Ok(());
    }
    // Hold _lock until tmux returns after the popup has closed.
    tmux::execute(
        tmux.command()
            .args(["display-popup", "-E", "-c"])
            .arg(client)
            .args([
                "-T",
                " Prefix help ",
                "-x",
                "#{client_width}",
                "-y",
                "#{client_height}",
                "-w",
            ])
            .arg(width.to_string())
            .arg("-h")
            .arg(height.to_string())
            .args([
                "-b",
                "rounded",
                "-s",
                "bg=default,fg=#e5e9f0",
                "-S",
                "fg=#88c0d0",
            ])
            .arg(command)
            .stderr(std::process::Stdio::null()),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delay_validation() {
        for value in ["", "-1", "1.5", "abc", "18446744073709551616"] {
            assert_eq!(delay(value), Duration::from_millis(500));
        }
        assert_eq!(delay("0"), Duration::ZERO);
        assert_eq!(delay("075"), Duration::from_millis(75));
    }

    #[test]
    fn popup_stays_inside_terminal() {
        assert_eq!(dimensions("160 50"), Some((70, 16)));
        assert_eq!(dimensions("40 8"), Some((40, 8)));
        for value in ["40 5", "0 10", "2 10", "", "40", "40 8 extra", "x 8"] {
            assert_eq!(dimensions(value), None);
        }
    }
}
