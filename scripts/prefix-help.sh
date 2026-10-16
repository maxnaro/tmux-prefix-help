#!/usr/bin/env bash
# Launched by a conditional status job when a client enters the prefix table.
# The popup forwards keyboard input through the native prefix table.
set -u

socket=$1
session=$2
server_pid=$3
client=$4

tmux_cmd() { tmux -S "$socket" "$@"; }
waiting() {
    [[ $(tmux_cmd display-message -p -c "$client" \
        '#{session_id}:#{client_prefix}' 2>/dev/null) == "$session:1" ]]
}

# Each attached client gets its own overlay, without changing pane geometry.
cache_dir=${XDG_CACHE_HOME:-$HOME/.cache}/tmux-prefix-help
mkdir -p "$cache_dir" || exit 0
client_lock=${client//[^[:alnum:]]/_}
exec 9>"$cache_dir/$server_pid-$client_lock.lock"
flock -n 9 || exit 0
waiting || exit 0

delay=$(tmux_cmd show-options -Av -t "$session" @prefix-help-delay 2>/dev/null)
[[ $delay =~ ^[0-9]+$ ]] || delay=500
# Check during the delay so a completed prefix cancels this job promptly.
for ((elapsed = 0; elapsed < delay; elapsed += 50)); do
    sleep 0.05
    waiting || exit 0
done

# Keep the popup compact and within the terminal without resizing any panes.
read -r width height < <(tmux_cmd display-message -p -c "$client" \
    '#{client_width} #{client_height}' 2>/dev/null)
[[ $width =~ ^[0-9]+$ && $height =~ ^[0-9]+$ ]] || exit 0
((height >= 6)) || exit 0
((height > 16)) && height=16
((width > 70)) && width=70
helper_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null || exit 0

waiting || exit 0
# -E lets the reader receive Escape/Ctrl-c itself. It closes the popup and
# forwards input in one command sequence, without a list of allowed shortcuts.
tmux_cmd display-popup -E -c "$client" -T ' Prefix help ' \
    -x '#{client_width}' -y '#{client_height}' -w "$width" -h "$height" \
    -b rounded -s 'bg=#1e222a,fg=#e5e9f0' -S 'fg=#88c0d0' \
    python3 "$helper_dir/prefix-help-popup.py" "$socket" "$client" "$session" \
    2>/dev/null
