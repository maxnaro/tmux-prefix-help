"""Keyboard decoding and real PTY checks, without needing a running tmux server."""

import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import tempfile
import time
import unittest


POPUP = Path(__file__).resolve().parents[1] / "scripts/prefix-help-popup.py"
SPEC = importlib.util.spec_from_file_location("prefix_popup", POPUP)
popup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(popup)


class DecodeTests(unittest.TestCase):
    def test_keyboard_encodings(self):
        cases = {
            b"1": "1", b";": ";", b"\x00": "C-@", b"\x02": "C-b",
            b"\x03": "C-c", b"\x09": "Tab", b"\x0d": "Enter", b"\x7f": "BSpace",
            "é".encode(): "é", "😺".encode(): "😺",
            b"\x1bh": "M-h", b"\x1b\x02": "M-C-b", b"\x1b;": "M-;",
            b"\x1b[A": "Up", b"\x1bOD": "Left", b"\x1b[1;5D": "C-Left",
            b"\x1b[1;3C": "M-Right", b"\x1b[1;2A": "S-Up",
            b"\x1b[3~": "Delete", b"\x1b[6~": "NPage", b"\x1b[Z": "BTab",
            b"\x1bOP": "F1", b"\x1b[24~": "F12", b"\x1b[15;5~": "C-F5",
            b"\x1bOp": "KP0", b"\x1bOM": "KPEnter",
            b"\x1b[65;6u": "C-S-A", b"\x1b[27;3;59~": "M-;",
        }
        for raw, key in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(popup.decode_key(raw + b"extra"), (key, len(raw)))

    def test_incomplete_keys_wait(self):
        for raw in (b"", b"\x1b", b"\x1b[", b"\x1b[1;", b"\x1bO", b"\xc3"):
            with self.subTest(raw=raw), self.assertRaises(popup.IncompleteKey):
                popup.decode_key(raw)

    def test_unknown_sequences_are_not_replayed_as_shortcuts(self):
        for raw in (b"\x1b[200~pasted text", b"\x1b[999~", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(popup.UnsupportedKey):
                popup.decode_key(raw)

    def test_close_precedes_replay_and_semicolons_are_escaped(self):
        command = popup.replay_command("/socket", "/dev/pts/1", [";", "M-;"])
        self.assertLess(command.index("display-popup"), command.index("send-keys"))
        self.assertEqual(command[-2:], ["\\;", "M-\\;"])


class LayoutTests(unittest.TestCase):
    def test_full_layout_has_all_groups_and_keys(self):
        lines = popup.hint_lines(68, 14)
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
        text = "\n".join(plain)
        for label in ("WINDOWS", "PANES", "COPY & SESSIONS", "TOOLS",
                      "1-9", "arrows", "All keybindings", "Esc to dismiss"):
            self.assertIn(label, text)
        self.assertIn("\x1b[1m", "".join(lines))

    def test_small_layouts_fit_and_keep_dismiss_hint(self):
        for width, height in ((90, 14), (64, 8), (40, 14), (25, 4)):
            with self.subTest(size=(width, height)):
                lines = popup.hint_lines(width, height)
                self.assertEqual(len(lines), height)
                for line in lines:
                    plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
                    self.assertLessEqual(len(plain), width - 1)
                self.assertIn("dismiss", lines[-1])


class PopupPTYTests(unittest.TestCase):
    def run_popup(self, chunks, session="$1"):
        with tempfile.TemporaryDirectory(prefix="prefix-popup-test-") as directory:
            root = Path(directory)
            log = root / "calls.jsonl"
            mock = root / "tmux"
            mock.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['MOCK_LOG'], 'a') as log:\n"
                "    log.write(json.dumps({'args': sys.argv[1:], 'own_session': os.getsid(0) == os.getpid()}) + '\\n')\n"
                "if 'display-message' in sys.argv: print(os.environ['MOCK_SESSION'])\n"
            )
            mock.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                       MOCK_LOG=str(log), MOCK_SESSION=session, PYTHONDONTWRITEBYTECODE="1")
            master, slave = pty.openpty()
            process = subprocess.Popen(
                ["python3", str(POPUP), "/socket", "/dev/pts/1", "$1"],
                stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env,
            )
            os.close(slave)
            try:
                rendered = b""
                deadline = time.monotonic() + 3
                while b"Esc to dismiss" not in rendered:
                    remaining = deadline - time.monotonic()
                    self.assertGreater(remaining, 0, rendered)
                    self.assertTrue(select.select([master], [], [], remaining)[0])
                    rendered += os.read(master, 4096)
                self.assertIn(b"\x1b[?2004h", rendered)
                self.assertNotIn(b"\x1b[?1000h", rendered)
                self.assertNotIn(b"\x1b[?1006h", rendered)
                for chunk in chunks:
                    os.write(master, chunk)
                    if len(chunks) > 1:
                        time.sleep(.01)
                _, stderr = process.communicate(timeout=3)
                self.assertEqual(process.returncode, 0, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                os.close(master)
            return [json.loads(line) for line in log.read_text().splitlines()]

    def test_unlisted_shortcut_and_queued_typing(self):
        calls = self.run_popup([b"1hello\r"])
        command = calls[-1]["args"]
        self.assertEqual(command[-7:], ["1", "h", "e", "l", "l", "o", "Enter"])
        self.assertIn("prefix", command)
        self.assertTrue(calls[-1]["own_session"])

    def test_split_arrow_and_unicode(self):
        for chunks, key in (([b"\x1b[", b"1;5D"], "C-Left"),
                            ([b"\xc3", b"\xa9"], "é")):
            with self.subTest(key=key):
                self.assertEqual(self.run_popup(chunks)[-1]["args"][-1], key)

    def test_control_c_is_forwarded(self):
        self.assertEqual(self.run_popup([b"\x03"])[-1]["args"][-1], "C-c")

    def test_alt_bracket_after_escape_timeout(self):
        self.assertEqual(self.run_popup([b"\x1b["])[-1]["args"][-1], "M-[")

    def test_bracketed_paste_does_not_execute_bindings(self):
        command = self.run_popup([b"\x1b[200~xkill\r\x1b[201~"])[-1]["args"]
        self.assertIn("display-message", command)
        self.assertNotIn("send-keys", command)

    def test_escape_cancels_to_root_without_replay(self):
        command = self.run_popup([b"\x1b"])[-1]["args"]
        self.assertEqual(command[-1], "root")
        self.assertNotIn("send-keys", command)

    def test_click_is_ignored_before_keyboard_input(self):
        for chunks in ([b"\x1b[<0;12;4M", b"1"], [b"\x1b[<", b"2;12;4M", b"1"]):
            with self.subTest(chunks=chunks):
                command = self.run_popup(chunks)[-1]["args"]
                self.assertEqual(command[-1], "1")
                self.assertIn("prefix", command)
                self.assertIn("send-keys", command)
                self.assertNotIn("display-message", command)

    def test_release_and_scroll_are_ignored_before_keyboard_input(self):
        calls = self.run_popup([b"\x1b[<0;12;4m", b"\x1b[<64;12;4M", b"1"])
        command = calls[-1]["args"]
        self.assertIn("prefix", command)
        self.assertEqual(command[-1], "1")

    def test_unknown_input_dismisses_with_explanation(self):
        command = self.run_popup([b"\x1b[999~"])[-1]["args"]
        self.assertIn("root", command)
        self.assertIn("display-message", command)
        self.assertNotIn("send-keys", command)

    def test_session_change_does_not_replay_input(self):
        calls = self.run_popup([b"x"], session="$2")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
