import json
import os
import subprocess
import sys
from pathlib import Path


def test_mcp_server():
    project_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    cmd = [sys.executable, "-m", "writing_context_rtfm.cli", "serve"]

    print(f"Starting server: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    init_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }

    try:
        print("Sending initialize...")
        proc.stdin.write(json.dumps(init_msg) + "\n")
        proc.stdin.flush()

        print("Waiting for response...")
        line = proc.stdout.readline()
        if not line:
            print("No response (EOF)")
            stderr = proc.stderr.read()
            print(f"Stderr: {stderr}")
            return

        print(f"Response: {line.strip()}")

        # Test tool list
        list_msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        print("Sending tools/list...")
        proc.stdin.write(json.dumps(list_msg) + "\n")
        proc.stdin.flush()

        line = proc.stdout.readline()
        print(f"Response: {line.strip()}")

    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    test_mcp_server()
