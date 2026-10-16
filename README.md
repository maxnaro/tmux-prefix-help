# tmux-prefix-help

Delayed, floating shortcut hints for tmux. Press your prefix (`Ctrl+b` by
default), pause for 500 ms, and a compact help popup appears at the bottom
right. It uses aligned groups, highlighted keys, and a dark background.
Panes and the status bar keep their sizes.

The next key closes the popup and goes through your existing tmux bindings,
including shortcuts not listed in the hints. `Esc` dismisses it and `?` opens
the complete binding list. Mouse input does not dismiss the popup.

## Requirements

- tmux with `display-popup` and `send-keys -K` (developed against tmux 3.6)
- Unix (Linux tested), Bash for the tmux/TPM entry point
- Rust 1.89 or newer and Cargo to build; the built executable needs only tmux

## Install

Clone the repository and build the Rust executable:

```sh
git clone https://github.com/maxnaro/tmux-prefix-help.git ~/.config/tmux/plugins/tmux-prefix-help
cargo build --release --locked --manifest-path ~/.config/tmux/plugins/tmux-prefix-help/Cargo.toml
```

Add these lines **after your status bar configuration** in `tmux.conf`:

```tmux
set -g @prefix-help-delay 500
run-shell '~/.config/tmux/plugins/tmux-prefix-help/prefix-help.tmux'
```

Reload with `tmux source-file ~/.config/tmux/tmux.conf`.

Alternatively, add `set -g @plugin 'maxnaro/tmux-prefix-help'` to a TPM setup.
After TPM clones the plugin, run the build command above and reload tmux.
Rebuild after updating the plugin. The entry point reports a missing binary
with the required build command.

The Rust loader preserves an existing delay setting and does not add duplicate
status triggers when loaded again. It replaces this checkout's old Bash status
trigger when upgrading an already running tmux server.

## Use as a submodule

From a dotfiles repository using the same tmux layout:

```sh
git submodule add https://github.com/maxnaro/tmux-prefix-help.git tmux/.config/tmux/plugins/tmux-prefix-help
git submodule update --init --recursive
cargo build --release --locked --manifest-path tmux/.config/tmux/plugins/tmux-prefix-help/Cargo.toml
```

Keep the loader in the parent tmux config; the implementation and its tests
live in this repository. Commit changes here first, then stage and commit the
updated submodule pointer in the parent repository.

## Behavior and limitations

`@prefix-help-delay` is measured in milliseconds. The popup is at most 70
columns wide and 16 rows high, and stays within the terminal. Below six rows
or three columns, no popup is shown. The loader sets `status-interval` to one
second so tmux can refresh its cached status jobs. Repeated prefixes can take
up to an extra second to trigger help.

The reader supports printable UTF-8, control/Alt keys, arrows, navigation keys,
F1-F12, keypad keys, and standard xterm modified-key sequences, including
CSI-u. Queued typing is replayed in order. Mouse reports are ignored and are
not forwarded to the pane underneath.

Unrecognized sequences and clipboard paste dismiss help with a message. Input
bursts are bounded at 64 KiB and 4,096 keys; rejected bursts are discarded in
full. Terminal-specific key encodings and typing exactly as the popup closes
still need testing on each terminal. The test suite includes both mocked PTY
checks and an isolated live tmux smoke test.

The displayed hints are currently a fixed list of common tmux shortcuts.
Forwarding uses the actual bindings; custom bindings do not require changing
the reader.

## Development

The implementation is one synchronous Rust executable. The shell entry point
invokes `load`; status jobs invoke `watch`; the popup invokes `popup`. Typed
decoder results distinguish incomplete input, unsupported input, keys, and
ignored mouse reports. File ownership holds client locks, and a terminal guard
restores terminal and keyboard settings. The crate forbids unsafe Rust and uses
`nix` and `terminal_size` for Unix APIs. See [DESIGN.md](DESIGN.md) for the design
and process boundaries.

Python 3 is used only for development tests (standard library only):

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked
python3 -m unittest discover -s tests -v
bash -n prefix-help.tmux
python3 tests/live_tmux.py
cargo build --release --locked
```

The Python tests use `target/debug/tmux-prefix-help`; set `PREFIX_HELP_BINARY`
to test a different executable, including the release build. The live test
creates and removes its own tmux socket, server, client PTY, and cache directory.
