"""Keyboard decoding and real PTY checks, without needing a running tmux server."""

import json
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import time
import termios
import unittest

from support import BINARY, require_binary


class PopupPTYTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_binary()

    def run_popup(self, chunks, session="$1", queued=b""):
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
            termios.tcsetwinsize(slave, (14, 68))
            previous = termios.tcgetattr(slave)
            if queued:
                os.write(master, queued)
            process = subprocess.Popen(
                [str(BINARY), "popup", "/socket", "/dev/pts/1", "$1"],
                stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env,
            )
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
                self.assertEqual(termios.tcgetattr(slave), previous)
                while select.select([master], [], [], 0)[0]:
                    rendered += os.read(master, 4096)
                for reset in (b"\x1b[?2004l", b"\x1b[>4;0m", b"\x1b[?25h"):
                    self.assertIn(reset, rendered)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                os.close(master)
                os.close(slave)
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

    def test_startup_preserves_queued_input(self):
        command = self.run_popup([], queued=b"1")[-1]["args"]
        self.assertEqual(command[-1], "1")

    def test_escape_then_queued_typing_goes_to_root(self):
        command = self.run_popup([b"\x1b[27uhello"])[-1]["args"]
        self.assertIn("root", command)
        self.assertEqual(command[-5:], list("hello"))

    def test_semicolons_are_escaped_for_tmux(self):
        command = self.run_popup([b";\x1b;"])[-1]["args"]
        self.assertEqual(command[-2:], ["\\;", "M-\\;"])

    def test_invalid_suffix_discards_the_entire_burst(self):
        command = self.run_popup([b"x\x1b[999~"])[-1]["args"]
        self.assertNotIn("send-keys", command)
        self.assertIn("root", command)

    def test_truncated_input_is_rejected(self):
        for raw in (b"\xc3", b"\x1b[1;", b"\xf0\x9f"):
            with self.subTest(raw=raw):
                command = self.run_popup([raw])[-1]["args"]
                self.assertNotIn("send-keys", command)
                self.assertIn("display-message", command)

    def test_sequence_limit_discards_burst(self):
        command = self.run_popup([b"\x1b[" + b"1" * 4097])[-1]["args"]
        self.assertNotIn("send-keys", command)
        self.assertIn("display-message", command)


if __name__ == "__main__":
    unittest.main()
