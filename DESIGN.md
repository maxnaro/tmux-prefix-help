# Rust rewrite

## Contract

Keep the existing delayed, per-client popup, fixed hints, native tmux binding
replay, Escape dismissal, paste rejection, mouse filtering, and session check.
Preserve the configured delay and append one status trigger on reload. The
terminal and pane sizes must not change.

## Structure

One synchronous `tmux-prefix-help` executable has `load`, `watch`, and `popup`
commands. `prefix-help.tmux` only locates the built executable and invokes
`load`, retaining the standalone and TPM entry point. Building is an explicit
installation step, so opening help never invokes Cargo.

- `tmux` owns command construction, configuration, and replay arguments.
- `watch` holds an advisory file lock for the popup's lifetime, checks prefix
  state every 50 ms during the configured delay, and clamps popup dimensions.
- `input` incrementally decodes bytes into typed keys or ignored mouse events.
  Incomplete and unsupported input are distinct results. Input and key counts
  are bounded; a rejected burst is discarded in full.
- `terminal` owns raw-mode restoration and the input wait loop. A guard restores
  termios, cursor visibility, and requested keyboard modes on normal return,
  errors, and panic unwinding. It preserves input already queued at startup.
- `layout` is a pure function returning bounded, styled hint lines.

Use `std` for processes, paths, timing, and file locks. Use `nix` for safe Unix
termios, select, and session APIs, and `terminal_size` for terminal dimensions.
There is no async runtime, general terminal UI framework, or CLI framework.
The crate forbids unsafe Rust. Rust 1.89 is the minimum because it provides
standard library file locking.

## Process and input boundaries

Pass tmux arguments directly to `Command`; quote paths only where tmux requires
a shell command (status jobs and popup launch). Keep OS paths as `OsString`
until a tmux format requires UTF-8, then report an explicit error.

The popup closes itself and replays keys as one tmux command sequence. A small
internal `replay` command starts a new Unix session before executing tmux. This
keeps replay alive when tmux closes the popup without an unsafe `pre_exec`
callback. Confirm the client is still in the original session before replay.
Escape selects the root table; other keys select the prefix table. Escape at
the beginning of a burst is consumed, while later queued keys still replay.
Escape trailing semicolons for tmux's argument parser, even without a shell.

Wait up to 60 ms for a partial UTF-8 or escape sequence. Complete input drains
without a typing delay. A standalone Escape and Alt-[ / Alt-O are resolved at
the deadline. Mouse reports alone keep waiting. Unsupported terminal input,
including bracketed paste, dismisses help without executing any of its keys.

## Compatibility and validation

The loader removes this checkout's previous Bash status trigger when migrating
an existing tmux configuration. No Python or `flock` executable is needed at
runtime. The Python standard library remains useful only for black-box tests
with a mock tmux and a real PTY.

Port decoder and layout examples to Rust unit tests. Exercise loader quoting
and idempotence, watcher cancellation/locking/sizing, popup bursts, split input,
terminal restoration, independent replay sessions, rejection paths, and session
changes against the executable. Finish with formatting, Clippy, release build,
and an isolated live tmux server smoke test. Linux is tested here; other Unix
platforms need their own live terminal verification.
