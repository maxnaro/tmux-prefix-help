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
- Bash, `flock`, and Python 3 (standard library only)

## Install

Clone the repository:

```sh
git clone https://github.com/maxnaro/tmux-prefix-help.git ~/.config/tmux/plugins/tmux-prefix-help
```

Add these lines **after your status bar configuration** in `tmux.conf`:

```tmux
set -g @prefix-help-delay 500
run-shell '~/.config/tmux/plugins/tmux-prefix-help/prefix-help.tmux'
```

Reload with `tmux source-file ~/.config/tmux/tmux.conf`.

Alternatively, add `set -g @plugin 'maxnaro/tmux-prefix-help'` to a TPM setup.
The entry point preserves an existing delay setting and does not add duplicate
status triggers when loaded again.

## Use as a submodule

From a dotfiles repository using the same tmux layout:

```sh
git submodule add https://github.com/maxnaro/tmux-prefix-help.git tmux/.config/tmux/plugins/tmux-prefix-help
git submodule update --init --recursive
```

Keep the loader in the parent tmux config; the implementation and its tests
live in this repository. Commit changes here first, then stage and commit the
updated submodule pointer in the parent repository.

## Behavior and limitations

`@prefix-help-delay` is measured in milliseconds. The popup is at most 70
columns wide and 16 rows high, and stays within the terminal. Below six rows,
no popup is shown. The loader sets `status-interval` to one second so tmux can
refresh its cached status jobs. Repeated prefixes can take up to an extra
second to trigger help.

The reader supports printable UTF-8, control/Alt keys, arrows, navigation keys,
F1-F12, keypad keys, and standard xterm modified-key sequences, including
CSI-u. Queued typing is replayed in order. Mouse reports are ignored and are
not forwarded to the pane underneath.

Unrecognized sequences and clipboard paste dismiss help with a message rather
than interpreting their bytes as shortcuts. Terminal-specific key encodings
and typing exactly as the popup closes still need live testing. Decoder and
PTY checks use a mocked tmux command; they do not replace live server testing.

The displayed hints are currently a fixed list of common tmux shortcuts.
Forwarding uses the actual bindings; custom bindings do not require changing
the reader.

## Development

```sh
python3 -m unittest discover -s tests -v
bash -n prefix-help.tmux scripts/prefix-help.sh
```
