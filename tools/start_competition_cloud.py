"""Compatibility entry point: all local competition services now run in Docker."""
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(["docker", "compose", "up", "-d", "--build", "--wait",
                                 "--wait-timeout", "180"], cwd=root)
    except FileNotFoundError:
        print("Docker CLI unavailable. Install/start Docker Desktop.", file=sys.stderr)
        sys.exit(1)
    if result.returncode == 0:
        print("Competition ready: http://127.0.0.1:8787")
    sys.exit(result.returncode)
