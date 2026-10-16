"""Smoke test against an isolated tmux 3.6+ server and an attached PTY client."""

import os
from pathlib import Path
import pty
import select
import shlex
import subprocess
import tempfile
import termios
import time

from support import BINARY, ROOT, require_binary


def main():
    require_binary()
    with tempfile.TemporaryDirectory(prefix="prefix-live-") as directory:
        root = Path(directory)
        socket = str(root / "tmux.sock")
        env = dict(os.environ, TERM="xterm-256color", XDG_CACHE_HOME=str(root / "cache"))
        env.pop("TMUX", None)

        def tmux(*args, check=True):
            result = subprocess.run(["tmux", "-S", socket, *args], env=env,
                                    capture_output=True, text=True, timeout=5)
            if check and result.returncode:
                raise RuntimeError(f"tmux {args!r}: {result.stderr.strip()}")
            return result.stdout.strip()

        master, slave = pty.openpty()
        termios.tcsetwinsize(slave, (30, 100))
        client_process = None
        rendered = bytearray()

        def drain():
            while select.select([master], [], [], 0)[0]:
                rendered.extend(os.read(master, 65536))

        def wait_for(predicate, description):
            deadline = time.monotonic() + 5
            while not predicate():
                drain()
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Timed out: {description}; output={bytes(rendered[-2000:])!r}")
                time.sleep(.02)
            drain()

        try:
            tmux("-f", "/dev/null", "new-session", "-d", "-s", "smoke", "-x", "100", "-y", "30", "cat")
            tmux("set-option", "-g", "@prefix-help-delay", "100")
            tmux("run-shell", shlex.join([str(BINARY), "load", str(ROOT)]))
            for key, option in (("1", "@smoke-bind"), (";", "@smoke-semicolon"), ("C-c", "@smoke-control"), ("x", "@smoke-danger")):
                tmux("bind-key", "-T", "prefix", "\\;" if key == ";" else key, "set-option", "-g", option, "yes")
            client_process = subprocess.Popen(["tmux", "-S", socket, "attach-session", "-t", "smoke"],
                                              stdin=slave, stdout=slave, stderr=slave, env=env)
            wait_for(lambda: bool(tmux("list-clients", "-F", "#{client_name}")), "client attachment")
            client = tmux("list-clients", "-F", "#{client_name}")
            geometry = tmux("display-message", "-p", "#{pane_width} #{pane_height}")

            def open_help():
                # Drain the previous frame so an old hint cannot satisfy the check.
                drain()
                rendered.clear()
                os.write(master, b"\x02")
                wait_for(lambda: b"WINDOWS" in rendered and b"Esc to dismiss" in rendered, "prefix help")
                assert tmux("display-message", "-p", "#{pane_width} #{pane_height}") == geometry

            def assert_option(option):
                wait_for(lambda: tmux("show-options", "-gqv", option) == "yes", option)
                wait_for(lambda: tmux("display-message", "-p", "-c", client, "#{client_key_table}") == "root", "root table")

            open_help()
            os.write(master, b"1hello\r")
            assert_option("@smoke-bind")
            wait_for(lambda: "hello" in tmux("capture-pane", "-p"), "queued typing in pane")

            open_help()
            os.write(master, b";")
            assert_option("@smoke-semicolon")

            open_help()
            os.write(master, b"\x03")
            assert_option("@smoke-control")

            open_help()
            os.write(master, b"\x1b")
            wait_for(lambda: tmux("display-message", "-p", "-c", client, "#{client_key_table}") == "root", "Escape dismissal")

            open_help()
            os.write(master, b"\x1b[200~x\x1b[201~")
            wait_for(lambda: tmux("display-message", "-p", "-c", client, "#{client_key_table}") == "root", "paste dismissal")
            assert not tmux("show-options", "-gqv", "@smoke-danger")
            assert tmux("display-message", "-p", "#{pane_width} #{pane_height}") == geometry
            print("Live tmux passed: delayed popup, native bindings, queued typing, semicolon, Ctrl-c, Escape, paste rejection, pane geometry")
        finally:
            tmux("kill-server", check=False)
            if client_process is not None:
                try:
                    client_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    client_process.kill()
                    client_process.wait()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    main()
