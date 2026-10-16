"""Paths shared by black-box tests; build once with cargo build --locked."""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("PREFIX_HELP_BINARY", ROOT / "target/debug/tmux-prefix-help")).resolve()


def require_binary():
    if not BINARY.is_file():
        raise RuntimeError("Build first with cargo build --locked (or set PREFIX_HELP_BINARY)")
