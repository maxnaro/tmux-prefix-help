use crate::input::{self, DecodeError, Key};
use crate::layout;
use nix::errno::Errno;
use nix::sys::select::{FD_SETSIZE, FdSet, select};
use nix::sys::termios::{self, SetArg, Termios};
use nix::sys::time::{TimeVal, TimeValLike};
use std::io::{self, Write};
use std::os::fd::{AsFd, AsRawFd, BorrowedFd};
use std::time::{Duration, Instant};

const MAX_BYTES: usize = 65_536;
const MAX_KEYS: usize = 4_096;
const ESCAPE_TIMEOUT: Duration = Duration::from_millis(60);

#[derive(Debug)]
pub enum Input {
    Keys(Vec<Key>),
    Unsupported,
}

struct RawTerminal<'fd> {
    fd: BorrowedFd<'fd>,
    previous: Termios,
}

impl<'fd> RawTerminal<'fd> {
    fn enter(fd: BorrowedFd<'fd>) -> io::Result<Self> {
        let previous = termios::tcgetattr(fd)?;
        let mut raw = previous.clone();
        termios::cfmakeraw(&mut raw);
        // Preserve bytes queued during process startup.
        termios::tcsetattr(fd, SetArg::TCSANOW, &raw)?;
        Ok(Self { fd, previous })
    }
}

impl Drop for RawTerminal<'_> {
    fn drop(&mut self) {
        let _ = termios::tcsetattr(self.fd, SetArg::TCSANOW, &self.previous);
        let mut output = io::stdout().lock();
        let _ = output.write_all(b"\x1b[?2004l\x1b[>4;0m\x1b[?25h\x1b[0m");
        let _ = output.flush();
    }
}

fn ready(fd: BorrowedFd<'_>, deadline: Option<Instant>) -> io::Result<bool> {
    if fd.as_raw_fd() < 0 || fd.as_raw_fd() as usize >= FD_SETSIZE {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "terminal descriptor is out of range",
        ));
    }
    loop {
        let mut fds = FdSet::new();
        fds.insert(fd);
        let mut timeout = deadline.map(|deadline| {
            let duration = deadline.saturating_duration_since(Instant::now());
            TimeVal::microseconds(duration.as_micros().min(i64::MAX as u128) as i64)
        });
        match select(None, &mut fds, None, None, timeout.as_mut()) {
            Ok(count) => return Ok(count > 0),
            Err(Errno::EINTR) => continue,
            Err(error) => return Err(error.into()),
        }
    }
}

fn finish(pending: &[u8], mut keys: Vec<Key>) -> Input {
    match input::finish(pending) {
        Ok(Some(key)) if keys.len() < MAX_KEYS => {
            keys.push(key);
            Input::Keys(keys)
        }
        Ok(None) => Input::Keys(keys),
        _ => Input::Unsupported,
    }
}

fn read_keys(fd: BorrowedFd<'_>) -> io::Result<Input> {
    let mut pending = Vec::new();
    let mut keys = Vec::new();
    let mut deadline = None;
    let mut total = 0;
    let mut chunk = [0u8; 4096];
    loop {
        if !ready(fd, deadline)? {
            return Ok(finish(&pending, keys));
        }
        let length = match nix::unistd::read(fd, &mut chunk) {
            Ok(0) => return Ok(finish(&pending, keys)),
            Ok(length) => length,
            Err(Errno::EINTR) => continue,
            Err(error) => return Err(error.into()),
        };
        total += length;
        if total > MAX_BYTES {
            return Ok(Input::Unsupported);
        }
        pending.extend_from_slice(&chunk[..length]);
        let mut consumed = 0;
        while consumed < pending.len() {
            match input::decode(&pending[consumed..]) {
                Ok(decoded) => {
                    if let Some(key) = decoded.key {
                        if keys.len() == MAX_KEYS {
                            return Ok(Input::Unsupported);
                        }
                        keys.push(key);
                    }
                    consumed += decoded.consumed;
                }
                Err(DecodeError::Incomplete) => break,
                Err(DecodeError::Unsupported) => return Ok(Input::Unsupported),
            }
        }
        pending.drain(..consumed);
        deadline = if !pending.is_empty() {
            Some(Instant::now() + ESCAPE_TIMEOUT)
        } else if !keys.is_empty() {
            Some(Instant::now())
        } else {
            total = 0;
            None
        };
    }
}

pub fn read_popup() -> io::Result<Input> {
    let stdin = io::stdin();
    let _terminal = RawTerminal::enter(stdin.as_fd())?;
    {
        let stdout = io::stdout();
        let (columns, rows) = terminal_size::terminal_size_of(&stdout)
            .map(|(width, height)| (usize::from(width.0), usize::from(height.0)))
            .filter(|(width, height)| *width > 0 && *height > 0)
            .unwrap_or((80, 24));
        let mut output = stdout.lock();
        // Bracketed paste marks input that must never execute as shortcuts.
        output.write_all(b"\x1b[2J\x1b[H\x1b[?25l\x1b[?2004h\x1b[>4;2m")?;
        for (index, line) in layout::hint_lines(columns, rows).iter().enumerate() {
            write!(output, "\x1b[{};1H{line}", index + 1)?;
        }
        output.flush()?;
    }
    read_keys(stdin.as_fd())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{self, File, OpenOptions};
    use std::io::{Seek, SeekFrom};

    fn queued(bytes: &[u8]) -> File {
        let path =
            std::env::temp_dir().join(format!("prefix-help-input-test-{}", std::process::id()));
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        // Unlink immediately; ownership of the open file handles cleanup.
        fs::remove_file(path).unwrap();
        file.write_all(bytes).unwrap();
        file.seek(SeekFrom::Start(0)).unwrap();
        file
    }

    #[test]
    fn reject_bursts_over_key_or_byte_limit() {
        // Queue everything before reading so the test does not depend on timing.
        let reader = queued(&vec![b'x'; MAX_KEYS + 1]);
        assert!(matches!(
            read_keys(reader.as_fd()).unwrap(),
            Input::Unsupported
        ));

        let mut bytes = vec![b'x'];
        while bytes.len() <= MAX_BYTES {
            bytes.extend_from_slice(b"\x1b[<0;1;1M");
        }
        let reader = queued(&bytes);
        assert!(matches!(
            read_keys(reader.as_fd()).unwrap(),
            Input::Unsupported
        ));
    }
}
