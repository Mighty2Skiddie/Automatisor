"""Entrypoint for Cloud Run: run the MCP server and the API in one container.

Cloud Run gives a service exactly one externally-routable port, but this system is two
processes by design — the agent is an MCP *client* and must not open the database. Both
still run here as separate processes speaking HTTP; the MCP server simply binds to
loopback inside the container instead of to a public address.

That keeps the protocol boundary intact (separate process, real HTTP, the same FastMCP
tools) while presenting one public surface. What it gives up is pointing MCP Inspector
at the deployed MCP endpoint — for that, run ``docker compose up`` locally, where the
two services are genuinely separate containers.

Why a Python supervisor rather than a shell script: the API must not start accepting
traffic before the MCP server is listening, or the first request 503s while Cloud Run
is still counting the container as healthy. This waits for the socket, with a bounded
timeout and a clear failure message.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

MCP_HOST = "127.0.0.1"
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))
STARTUP_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    """Block until something accepts a TCP connection, or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(POLL_INTERVAL_SECONDS)
    return False


def main() -> int:
    # Cloud Run injects PORT and expects the container to listen on it.
    api_port = os.environ.get("PORT", "8000")

    # The agent reaches the database only through this URL, in-container.
    os.environ.setdefault("MCP_HOST", MCP_HOST)
    os.environ.setdefault("MCP_PORT", str(MCP_PORT))
    os.environ["MCP_SERVER_URL"] = f"http://{MCP_HOST}:{MCP_PORT}/mcp"

    print(f"starting MCP server on {MCP_HOST}:{MCP_PORT}", flush=True)
    mcp = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server.server"],
        env=os.environ.copy(),
    )

    if not wait_for_port(MCP_HOST, MCP_PORT, STARTUP_TIMEOUT_SECONDS):
        mcp.terminate()
        print(
            f"MCP server did not listen on {MCP_HOST}:{MCP_PORT} within "
            f"{STARTUP_TIMEOUT_SECONDS:.0f}s; aborting so Cloud Run reports a failed "
            "revision rather than serving 503s",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"MCP up; starting API on 0.0.0.0:{api_port}", flush=True)
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(api_port),
        ],
        env=os.environ.copy(),
    )

    try:
        # If either process dies the container should die too, so Cloud Run replaces
        # the whole revision rather than leaving a half-working instance serving errors.
        while True:
            if (code := mcp.poll()) is not None:
                print(f"MCP server exited ({code}); shutting down", file=sys.stderr, flush=True)
                api.terminate()
                return code or 1
            if (code := api.poll()) is not None:
                print(f"API exited ({code}); shutting down", file=sys.stderr, flush=True)
                mcp.terminate()
                return code or 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        api.terminate()
        mcp.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
