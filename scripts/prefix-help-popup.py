#!/usr/bin/env python3
"""Show prefix hints and replay keyboard input after closing the tmux popup."""

import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty


class IncompleteKey(Exception):
    pass


class UnsupportedKey(Exception):
    pass


ARROWS = dict(zip("ABCDHF", ("Up", "Down", "Right", "Left", "Home", "End")))
TILDE = {1: "Home", 2: "Insert", 3: "Delete", 4: "End", 5: "PPage", 6: "NPage",
         7: "Home", 8: "End", 11: "F1", 12: "F2", 13: "F3", 14: "F4",
         15: "F5", 17: "F6", 18: "F7", 19: "F8", 20: "F9", 21: "F10",
         23: "F11", 24: "F12"}
SS3 = {**ARROWS, **dict(zip("PQRS", ("F1", "F2", "F3", "F4"))),
       **dict(zip("pqrstuvwxy", ("KP0", "KP1", "KP2", "KP3", "KP4",
                                  "KP5", "KP6", "KP7", "KP8", "KP9"))),
       "M": "KPEnter", "n": "KP.", "o": "KP/", "j": "KP*", "m": "KP-", "k": "KP+"}


def modified(key, modifier=1):
    """Xterm modifier values are 1 + shift/alt/control bit flags."""
    bits = modifier - 1
    if bits < 0 or bits & ~7:
        raise UnsupportedKey("unsupported modifier")
    return ("C-" if bits & 4 else "") + ("M-" if bits & 2 else "") + ("S-" if bits & 1 else "") + key


def character(code):
    if code in (9, 13, 27, 127):
        return {9: "Tab", 13: "Enter", 27: "Escape", 127: "BSpace"}[code]
    if code < 32:
        return "C-" + chr(code + 64).lower()
    if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        raise UnsupportedKey("invalid character")
    return chr(code)


def decode_key(data):
    """Return (tmux key name, bytes consumed), or wait for a complete sequence."""
    if not data:
        raise IncompleteKey
    first = data[0]
    if first != 27:
        if first >= 128 and not 194 <= first <= 244:
            raise UnsupportedKey("invalid UTF-8 lead byte")
        length = 1 if first < 128 else 2 if first < 224 else 3 if first < 240 else 4
        if len(data) < length:
            raise IncompleteKey
        try:
            return character(ord(data[:length].decode("utf-8"))), length
        except (UnicodeError, TypeError) as error:
            raise UnsupportedKey("invalid UTF-8") from error
    if len(data) == 1:
        raise IncompleteKey
    if data.startswith(b"\x1b[<"):
        mouse = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", data)
        if not mouse:
            if len(data) < 64 and re.fullmatch(rb"\x1b\[<[0-9;]*", data):
                raise IncompleteKey
            raise UnsupportedKey("invalid mouse event")
        # Ignore any stray mouse reports; clicks never dismiss or replay keys.
        return None, mouse.end()
    if data[1:2] == b"[":
        match = re.match(rb"\x1b\[([0-9;]*)([@-~])", data)
        if not match:
            if len(data) < 64 and re.fullmatch(rb"\x1b\[[0-9;]*", data):
                raise IncompleteKey
            raise UnsupportedKey("unknown CSI sequence")
        params = [int(p) if p else 1 for p in match[1].split(b";")] if match[1] else []
        final = chr(match[2][0])
        modifier = params[1] if len(params) > 1 else 1
        if final in ARROWS:
            key = modified(ARROWS[final], modifier)
        elif final in "PQRS":
            key = modified("F" + str("PQRS".index(final) + 1), modifier)
        elif final == "Z":
            key = "BTab"
        elif final == "u" and 1 <= len(params) <= 2:
            key = modified(character(params[0]), modifier)
        elif final == "~" and len(params) == 3 and params[0] == 27:
            key = modified(character(params[2]), params[1])
        elif final == "~" and params and params[0] in TILDE:
            key = modified(TILDE[params[0]], modifier)
        else:
            raise UnsupportedKey("unknown CSI key")
        return key, match.end()
    if data[1:2] == b"O":
        if len(data) < 3:
            raise IncompleteKey
        if chr(data[2]) not in SS3:
            raise UnsupportedKey("unknown SS3 key")
        return SS3[chr(data[2])], 3
    key, length = decode_key(data[1:])
    return "M-" + key, length + 1


def read_keys(fd):
    """Read a key and any queued burst; wait briefly for split escape/UTF-8 input."""
    pending = b""
    keys = []
    deadline = None
    while True:
        timeout = None if deadline is None else max(0, deadline - time.monotonic())
        if not select.select([fd], [], [], timeout)[0]:
            if pending == b"\x1b":
                keys.append("Escape")
            elif pending in (b"\x1b[", b"\x1bO"):
                keys.append("M-" + chr(pending[1]))
            elif pending:
                raise UnsupportedKey("incomplete terminal key")
            return keys
        chunk = os.read(fd, 4096)
        if not chunk:
            return keys
        pending += chunk
        if len(pending) > 65536 or len(keys) > 4096:
            raise UnsupportedKey("input burst too large")
        while pending:
            try:
                key, length = decode_key(pending)
            except IncompleteKey:
                break
            if key is not None:
                keys.append(key)
            pending = pending[length:]
        # Complete queued input is drained without an extra typing delay.
        deadline = time.monotonic() + (0.06 if pending else 0) if pending or keys else None


def replay_command(socket, client, keys, error=None):
    cancelled = not keys or keys[0] == "Escape"
    if cancelled and keys:
        keys = keys[1:]
    args = ["tmux", "-S", socket, "display-popup", "-C", "-c", client,
            ";", "switch-client", "-c", client, "-T", "root" if cancelled else "prefix"]
    if keys:
        # tmux's argv parser treats a trailing semicolon specially, even when
        # passed without a shell. Escape modified semicolons as well.
        args += [";", "send-keys", "-K", "-c", client, "--",
                 *(key[:-1] + "\\;" if key.endswith(";") else key for key in keys)]
    if error:
        args += [";", "display-message", "-c", client,
                 "Prefix help: unsupported terminal input; press prefix again"]
    return args


GROUPS = (
    ("WINDOWS", (("c", "New window"), ("n / p", "Next / previous"),
                 ("1-9", "Select window"), ("w", "Choose window"), (",", "Rename window"))),
    ("PANES", (("%", "Split left / right"), ('"', "Split top / bottom"),
               ("arrows", "Focus pane"), ("z", "Zoom / unzoom"), ("x", "Close pane"))),
    ("COPY & SESSIONS", (("[", "Copy / scrollback"), ("]", "Paste buffer"),
                         ("s", "Choose session"), ("d", "Detach"))),
    ("TOOLS", (("r", "Reload config"), (":", "Command prompt"),
               ("?", "All keybindings"), ("Esc", "Dismiss help"))),
)


def styled(text, color, bold=False):
    return f"\x1b[38;2;{color}m" + ("\x1b[1m" if bold else "") + text + "\x1b[39;22m"


def hint_lines(columns, rows):
    """Lay out aligned groups, reserving a footer even in small terminals."""
    width = max(0, columns - 1)
    margin = 2 if width >= 30 else 0
    usable = max(0, width - margin * 2)
    cyan, muted = "136;192;208", "146;158;178"

    def heading(group, cell_width):
        text = group[0][:cell_width].ljust(cell_width)
        return styled(text, muted, True)

    def item(pair, cell_width):
        key, label = pair
        key_width = min(8, cell_width)
        text_width = max(0, cell_width - key_width)
        return styled(key[:key_width].ljust(key_width), cyan, True) + label[:text_width].ljust(text_width)

    lines = []
    if usable >= 62:
        gap = 4
        cell_width = (usable - gap) // 2
        for left, right in ((GROUPS[0], GROUPS[1]), (GROUPS[2], GROUPS[3])):
            lines.append(" " * margin + heading(left, cell_width) + " " * gap + heading(right, cell_width))
            for left_item, right_item in zip(left[1], right[1]):
                lines.append(" " * margin + item(left_item, cell_width) + " " * gap + item(right_item, cell_width))
            lines.append("")
    else:
        for group in GROUPS:
            lines.append(" " * margin + heading(group, usable))
            lines.extend(" " * margin + item(pair, usable) for pair in group[1])
            lines.append("")
    footer = "Any key works  /  ? all bindings  /  Esc to dismiss"
    if len(footer) > usable:
        footer = "? All bindings  /  Esc to dismiss"
    if len(footer) > usable:
        footer = "Esc to dismiss"
    content_rows = max(0, rows - 1)
    return (lines[:content_rows] + [""] * max(0, content_rows - len(lines)) +
            [" " * margin + styled(footer[:usable], muted)])[:rows]


def draw():
    columns, rows = shutil.get_terminal_size()
    # Mark paste explicitly so it cannot masquerade as a burst of shortcuts.
    sys.stdout.write("\x1b[2J\x1b[H\x1b[?25l\x1b[?2004h\x1b[>4;2m")
    for index, line in enumerate(hint_lines(columns, rows)):
        sys.stdout.write(f"\x1b[{index + 1};1H{line}")
    sys.stdout.flush()


def main():
    socket, client, session = sys.argv[1:]
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    keys, error = [], None
    try:
        # TCSANOW preserves keys queued while Python was starting.
        tty.setraw(fd, termios.TCSANOW)
        draw()
        keys = read_keys(fd)
    except UnsupportedKey as caught:
        error = caught
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, previous)

    # Never replay into a client that changed sessions while help was open.
    current = subprocess.run(["tmux", "-S", socket, "display-message", "-p", "-c", client,
                              "#{session_id}"], capture_output=True, text=True)
    if current.returncode or current.stdout.strip() != session:
        return
    # The close and replay form one tmux command sequence. Give that client its
    # own process group so closing this popup cannot kill it halfway through.
    process = subprocess.Popen(replay_command(socket, client, keys, error),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    process.wait()


if __name__ == "__main__":
    main()
