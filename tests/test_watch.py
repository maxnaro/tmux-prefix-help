"""Exercise the watcher process, including lock lifetime, with a mock tmux."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest

from support import BINARY, require_binary


class WatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_binary()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="prefix-watch-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.log = self.root / "calls.jsonl"
        self.state = self.root / "state.json"
        self.update(waiting="$1:1", delay="0", dimensions="120 40", hold=False)
        mock = self.root / "tmux"
        mock.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys, time\n"
            "root = pathlib.Path(os.environ['MOCK_ROOT'])\n"
            "args = sys.argv[1:]\n"
            "with (root / 'calls.jsonl').open('a') as log: log.write(json.dumps(args) + '\\n')\n"
            "state = json.loads((root / 'state.json').read_text())\n"
            "if 'show-options' in args: print(state['delay'])\n"
            "elif 'display-message' in args:\n"
            "    print(state['dimensions'] if args[-1] == '#{client_width} #{client_height}' else state['waiting'])\n"
            "elif state['hold']:\n"
            "    deadline = time.monotonic() + 5\n"
            "    while not (root / 'release').exists() and time.monotonic() < deadline: time.sleep(.01)\n"
        )
        mock.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                        MOCK_ROOT=str(self.root), XDG_CACHE_HOME=str(self.root / "cache"))

    def update(self, **state):
        current = json.loads(self.state.read_text()) if self.state.exists() else {}
        current.update(state)
        next_state = self.root / "next.json"
        next_state.write_text(json.dumps(current))
        next_state.replace(self.state)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def start(self, client="/dev/pts/1"):
        process = subprocess.Popen([str(BINARY), "watch", "/sock'et with spaces", "$1", "123", client],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    def finished(self, process):
        _, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            self.assertLess(time.monotonic(), deadline, self.calls())
            time.sleep(.01)

    def test_popup_dimensions_and_shell_arguments(self):
        self.finished(self.start())
        args = self.calls()[-1]
        self.assertIn("display-popup", args)
        self.assertIn("-E", args)
        self.assertEqual(args[args.index("-w") + 1], "70")
        self.assertEqual(args[args.index("-h") + 1], "16")
        self.assertEqual(shlex.split(args[-1]), [str(BINARY), "popup", "/sock'et with spaces", "/dev/pts/1", "$1"])

    def test_small_and_invalid_terminals_do_not_open_popup(self):
        for dimensions in ("40 5", "2 10", "invalid"):
            with self.subTest(dimensions=dimensions):
                self.update(dimensions=dimensions)
                self.finished(self.start())
        self.assertFalse(any("display-popup" in args for args in self.calls()))

    def test_prefix_completion_cancels_during_delay(self):
        self.update(delay="1000")
        process = self.start()
        self.wait_for(lambda: any("show-options" in args for args in self.calls()))
        self.update(waiting="$1:0")
        self.finished(process)
        self.assertFalse(any("display-popup" in args for args in self.calls()))

    def test_changed_session_cancels(self):
        self.update(waiting="$2:1")
        self.finished(self.start())
        self.assertEqual(len(self.calls()), 1)

    def test_lock_held_through_popup_and_other_clients_are_independent(self):
        self.update(hold=True)
        first = self.start("a/b")
        self.wait_for(lambda: any("display-popup" in args for args in self.calls()))
        before = len(self.calls())
        self.finished(self.start("a/b"))
        self.assertEqual(len(self.calls()), before)
        # These client names collided under the previous punctuation replacement.
        other = self.start("a?b")
        self.wait_for(lambda: sum("display-popup" in args for args in self.calls()) == 2)
        (self.root / "release").touch()
        self.finished(first)
        self.finished(other)
        self.finished(self.start("a/b"))
        self.assertEqual(sum("display-popup" in args for args in self.calls()), 3)


if __name__ == "__main__":
    unittest.main()
