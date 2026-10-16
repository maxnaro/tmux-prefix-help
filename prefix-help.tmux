#!/usr/bin/env bash
# Standalone / TPM entry point. Run after configuring the status bar.
set -euo pipefail

plugin_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -z $(tmux show-options -gqv @prefix-help-delay) ]]; then
    tmux set-option -g @prefix-help-delay 500
fi
tmux set-option -g status-interval 1

# Quote the script path for the POSIX shell used by tmux status jobs.
helper="$plugin_dir/scripts/prefix-help.sh"
helper=${helper//\'/\'\\\'\'}
trigger="#{?client_prefix,#(bash '$helper' #{q:socket_path} #{q:session_id} #{pid} #{q:client_name}),}"
status_right=$(tmux show-options -gqv status-right)
if [[ $status_right != *"$trigger"* ]]; then
    tmux set-option -ag status-right "$trigger"
fi
