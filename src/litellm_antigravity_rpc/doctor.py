"""Bounded ACP initialization probe. Never authenticates or submits prompts."""

import asyncio
import json
import math
import tempfile
from collections.abc import Sequence

from . import __version__


class ProbeError(RuntimeError):
    pass


async def probe(command: Sequence[str], timeout: float = 15) -> dict:
    if not command or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("A command and positive timeout are required.")
    with tempfile.TemporaryDirectory(prefix="antigravity-rpc-doctor-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )
        try:
            async with asyncio.timeout(timeout):
                message = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {
                            "name": "litellm-antigravity-rpc",
                            "version": __version__,
                        },
                    },
                }
                proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                await proc.stdin.drain()
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        raise ProbeError("ACP exited before initialize completed.")
                    try:
                        reply = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise ProbeError("ACP stdout is not valid JSON.") from None
                    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
                        raise ProbeError("Invalid JSON-RPC envelope.")
                    if "method" in reply:
                        if "id" in reply:
                            raise ProbeError(
                                "Unexpected server request during initialize."
                            )
                        continue
                    if type(reply.get("id")) is not int or reply["id"] != 1:
                        raise ProbeError("Unexpected response ID.")
                    result = reply.get("result")
                    if "error" in reply or not isinstance(result, dict):
                        raise ProbeError(
                            "ACP initialize failed; remote details omitted."
                        )
                    if (
                        type(result.get("protocolVersion")) is not int
                        or result["protocolVersion"] != 1
                    ):
                        raise ProbeError("ACP protocol version 1 was not negotiated.")
                    if not isinstance(result.get("agentCapabilities"), dict):
                        raise ProbeError("Missing or invalid agent capabilities.")
                    auth_methods = result.get("authMethods", [])
                    if not isinstance(auth_methods, list) or any(
                        not isinstance(entry, dict)
                        or not isinstance(entry.get("id"), str)
                        for entry in auth_methods
                    ):
                        raise ProbeError("Invalid authentication methods.")
                    return {
                        "status": "ok",
                        "protocolVersion": 1,
                        "agentInfo": result.get("agentInfo", {}),
                        "agentCapabilities": result["agentCapabilities"],
                        "authMethodIds": [entry["id"] for entry in auth_methods],
                        "authenticationVerified": False,
                        "modelAccessVerified": False,
                    }
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
