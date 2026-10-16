"""Check standalone loading and reload behavior without a live tmux server."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from support import BINARY, ROOT, require_binary

LOADER = ROOT / "prefix-help.tmux"


class LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_binary()

    def test_load_is_idempotent_and_preserves_user_settings(self):
        for delay, legacy in ((None, False), ("750", False), ("750", True)):
            with self.subTest(delay=delay, legacy=legacy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plugin = root / "plugin's folder"
                plugin.mkdir()
                shutil.copy2(LOADER, plugin / LOADER.name)
                binary = plugin / "target/release/tmux-prefix-help"
                binary.parent.mkdir(parents=True)
                shutil.copy2(BINARY, binary)
                state_file = root / "state.json"
                initial = {"status-right": "existing status"}
                if legacy:
                    helper = str(plugin / "scripts/prefix-help.sh").replace("'", "'\\''")
                    initial["status-right"] += (
                        "#{?client_prefix,#(bash '" + helper + "' #{q:socket_path} "
                        "#{q:session_id} #{pid} #{q:client_name}),}"
                    )
                if delay is not None:
                    initial["@prefix-help-delay"] = delay
                state_file.write_text(json.dumps(initial))
                mock = root / "tmux"
                mock.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, os, pathlib, sys\n"
                    "path = pathlib.Path(os.environ['MOCK_STATE'])\n"
                    "state = json.loads(path.read_text())\n"
                    "args = sys.argv[1:]\n"
                    "if args[0] == 'show-options': print(state.get(args[-1], ''))\n"
                    "else:\n"
                    "    key, value = args[-2:]\n"
                    "    state[key] = state.get(key, '') + value if '-ag' in args else value\n"
                    "    path.write_text(json.dumps(state))\n"
                )
                mock.chmod(0o755)
                env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                           MOCK_STATE=str(state_file))
                for _ in range(2):
                    subprocess.run(["bash", str(plugin / LOADER.name)], env=env, check=True)
                state = json.loads(state_file.read_text())
                self.assertEqual(state["@prefix-help-delay"], delay or "500")
                self.assertEqual(state["status-interval"], "1")
                self.assertTrue(state["status-right"].startswith("existing status"))
                self.assertEqual(state["status-right"].count("#{?client_prefix,"), 1)
                command = state["status-right"].split("#(", 1)[1].rsplit("),}", 1)[0]
                self.assertEqual(shlex.split(command)[:2], [str(binary), "watch"])
                self.assertNotIn("scripts/prefix-help.sh", state["status-right"])

    def test_missing_binary_explains_build_step(self):
        with tempfile.TemporaryDirectory() as directory:
            loader = Path(directory) / LOADER.name
            shutil.copy2(LOADER, loader)
            result = subprocess.run(["bash", str(loader)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cargo build --release --locked", result.stderr)


if __name__ == "__main__":
    unittest.main()
