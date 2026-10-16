"""Check standalone loading and reload behavior without a live tmux server."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


LOADER = Path(__file__).resolve().parents[1] / "prefix-help.tmux"


class LoaderTests(unittest.TestCase):
    def test_load_is_idempotent_and_preserves_user_settings(self):
        for delay in (None, "750"):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plugin = root / "plugin's folder"
                plugin.mkdir()
                shutil.copy2(LOADER, plugin / LOADER.name)
                state_file = root / "state.json"
                initial = {"status-right": "existing status"}
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
                self.assertEqual(shlex.split(command)[1], str(plugin / "scripts/prefix-help.sh"))


if __name__ == "__main__":
    unittest.main()
