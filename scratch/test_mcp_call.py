import json
import os
import subprocess
import sys
from pathlib import Path


def test_mcp_server_call():
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

    def send(msg):
        print(f"Sending: {msg.get('method', 'response')}")
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def recv():
        print("Waiting for response...")
        line = proc.stdout.readline()
        if not line:
            print("No response (EOF)")
            stderr = proc.stderr.read()
            print(f"Stderr: {stderr}")
            return None
        return json.loads(line)

    try:
        # Initialize
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            }
        )
        recv()

        # Call tool
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_writing_context_pack", "arguments": {"task": "Test task"}},
            }
        )
        res = recv()
        if res:
            print(f"Result: {json.dumps(res, indent=2)}")

    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    test_mcp_server_call()
