#!/usr/bin/env bash
# Standalone / TPM entry point. Build the Rust binary before loading.
set -euo pipefail

plugin_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
binary="$plugin_dir/target/release/tmux-prefix-help"
if [[ ! -x $binary ]]; then
    printf 'tmux-prefix-help: build first with cargo build --release --locked --manifest-path "%s/Cargo.toml"\n' "$plugin_dir" >&2
    exit 1
fi
exec "$binary" load "$plugin_dir"
