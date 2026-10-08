#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 - "$PROJECT_ROOT" <<'PY'
import os, signal, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1]).resolve()
pids = set()
for port in (3000, 8000):
    listing = subprocess.run(['lsof', '-nP', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'], capture_output=True, text=True)
    for pid in listing.stdout.split():
        cwd = subprocess.run(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'], capture_output=True, text=True)
        paths = [line[1:] for line in cwd.stdout.splitlines() if line.startswith('n')]
        if not paths or not all(Path(path).resolve().is_relative_to(root) for path in paths):
            raise SystemExit(f'Port {port} belongs to another application; refusing to stop it.')
        pids.add(int(pid))
for pid in pids:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
for _ in range(50):
    active = any(subprocess.run(['lsof', '-nP', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'], capture_output=True).stdout for port in (3000, 8000))
    if not active:
        break
    time.sleep(.1)
else:
    raise SystemExit('Services have not stopped; inspect the terminal output and try again.')
PY
exec bash "$PROJECT_ROOT/scripts/dev.sh"
