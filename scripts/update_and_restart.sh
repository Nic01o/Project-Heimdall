#!/usr/bin/env bash
# Stops the running alarmclock daemon, updates the repo to the latest
# origin/master (without losing uncommitted changes to tracked files
# such as config/config.yaml), and starts the daemon again.
#
# data/ and rpi_config.txt are gitignored and are never touched by git,
# so alarms and webui-changed settings survive regardless.

set -euo pipefail

main() {
    local project_dir
    project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    cd "$project_dir"

    local daemon_pattern="alarmclock.daemon"
    local venv_py="$project_dir/.venv/bin/python"
    local log_file="$project_dir/data/daemon.log"

    stop_daemon "$daemon_pattern"
    update_repo
    start_daemon "$venv_py" "$log_file"
}

stop_daemon() {
    local pattern="$1"
    local pids
    pids="$(pgrep -f "$pattern" || true)"

    if [ -z "$pids" ]; then
        echo "==> Daemon is not running, nothing to stop."
        return 0
    fi

    echo "==> Stopping daemon (PID(s): $pids)..."
    kill $pids 2>/dev/null || true

    local waited=0
    while pgrep -f "$pattern" >/dev/null 2>&1; do
        if [ "$waited" -ge 10 ]; then
            echo "==> Daemon did not stop gracefully after 10s, sending SIGKILL..."
            pkill -9 -f "$pattern" || true
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo "==> Daemon stopped."
}

update_repo() {
    echo "==> Fetching origin..."
    git fetch origin

    local stashed=0
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        echo "==> Local changes to tracked files detected, stashing..."
        git stash push -m "auto-stash before update $(date -Iseconds)"
        stashed=1
    fi

    echo "==> Checking out master..."
    git checkout master

    echo "==> Pulling latest master..."
    git pull origin master

    if [ "$stashed" -eq 1 ]; then
        echo "==> Restoring stashed local changes..."
        if ! git stash pop; then
            echo "!! Conflict while restoring local changes." >&2
            echo "!! Your changes are safe in the stash (see 'git stash list')." >&2
            echo "!! Resolve the conflict manually, then re-run this script to restart the daemon." >&2
            exit 1
        fi
    fi
}

start_daemon() {
    local venv_py="$1"
    local log_file="$2"

    echo "==> Starting daemon..."
    mkdir -p "$(dirname "$log_file")"
    # Run as a module (not a direct script path) so 'alarmclock.*' absolute
    # imports resolve - this matches how README documents running it and
    # requires the project root (cwd here, see main()) to be on sys.path.
    nohup "$venv_py" -m alarmclock.daemon >>"$log_file" 2>&1 &
    disown
    echo "==> Daemon started with PID $!. Logs: $log_file"
}

main "$@"
