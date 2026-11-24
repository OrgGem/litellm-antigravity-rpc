"""Stage-1 research probe, not a production ACP transport.

Default sends no prompts. Explicit prompt/cancel/tool probes consume quota via
the CLI's own login. Changing HOME does NOT isolate the OS credential manager.
Never authenticates, logs out, or reads credential files. Review captured output
before committing; the sanitizer is specific to these fixed synthetic prompts.
"""

import argparse
import asyncio
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


async def capture(
    executable,
    cached_login=False,
    prompt_probe=False,
    cancel_probe=False,
    tool_probe=False,
    fresh_state=False,
    retention_probe=False,
    canary_probe=False,
    outside_canary=False,
    usage_probe=False,
    permission_probe=False,
):
    records = []
    with tempfile.TemporaryDirectory(prefix="agy-contract-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        if prompt_probe:
            agent_dir = workspace / ".agents" / "agents"
            agent_dir.mkdir(parents=True)
            agent_dir.joinpath("contract-text-only.md").write_text(
                "---\nname: contract-text-only\ndescription: Text-only protocol probe\n"
                + ("tools: [run_command]\n" if permission_probe else "tools: []\n")
                + "mainAgent: true\nsubagent: false\n"
                "mcpServers: []\nskills: []\nplugins: []\n"
                "commandExecutionPolicy: off\n---\n"
                + (
                    "Attempt the requested terminal command once. If denied, stop.\n"
                    if permission_probe
                    else "Respond with plain text only. Never invoke tools.\n"
                ),
                encoding="utf-8",
            )
        command = [
            executable,
            "acp",
            "--disable-telemetry",
            "--include-default-customizations=false",
            "--log-file",
            str(root / "acp.log"),
        ]
        env = dict(os.environ)
        if prompt_probe:
            command += ["--agent", "contract-text-only"]
        if not cached_login:
            # Child-only environment; never alter the user's HOME or credentials.
            env = {
                k: v
                for k, v in env.items()
                if k.upper()
                in {
                    "SYSTEMROOT",
                    "WINDIR",
                    "PATH",
                    "PATHEXT",
                    "COMSPEC",
                    "TEMP",
                    "TMP",
                    "SYSTEMDRIVE",
                    "NUMBER_OF_PROCESSORS",
                }
            }
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
                folder = root / key.lower()
                folder.mkdir()
                env[key] = str(folder)
            command += [
                "--app-data-dir",
                "contract-probe",
                "--gemini-dir",
                str(root / "gemini") if fresh_state else ".gemini",
            ]
        if canary_probe:
            # Only synthetic files under this run's temp directory are touched.
            canary = root / "canary.py"
            canary.write_text(
                "import pathlib, sys\n"
                "pathlib.Path(sys.argv[1]).write_text('started')\n"
                "print('{}')\n",
                encoding="utf-8",
            )
            scopes = {
                "workspace": workspace / ".agents",
                "global": root / "gemini" / "config",
            }
            if outside_canary:
                scopes = {
                    "home": root / "home" / ".gemini" / "config",
                    "userprofile": root / "userprofile" / ".gemini" / "config",
                    "parent": root / ".agents",
                }
            for scope, folder in scopes.items():
                folder.mkdir(parents=True, exist_ok=True)
                argv = [sys.executable, str(canary), str(root / f"{scope}-hook.marker")]
                hook_command = (
                    subprocess.list2cmdline(argv)
                    if os.name == "nt"
                    else shlex.join(argv)
                )
                folder.joinpath("hooks.json").write_text(
                    json.dumps(
                        {
                            "contract-canary": {
                                "SessionStart": [
                                    {
                                        "type": "command",
                                        "command": hook_command,
                                        "timeout": 3,
                                    }
                                ]
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                folder.joinpath("mcp_config.json").write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "contract-canary": {
                                    "command": sys.executable,
                                    "args": [
                                        str(canary),
                                        str(root / f"{scope}-mcp.marker"),
                                    ],
                                    "timeoutSeconds": 2,
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
        if permission_probe:
            for name in ("antigravity-cli", "contract-probe"):
                settings_dir = root / "gemini" / name
                settings_dir.mkdir(parents=True, exist_ok=True)
                settings_dir.joinpath("settings.json").write_text(
                    json.dumps(
                        {
                            "agentMode": "default",
                            "toolPermission": "strict",
                            "allowNonWorkspaceAccess": False,
                        }
                    ),
                    encoding="utf-8",
                )
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )
        replacements = {str(root): "<TEMP>", str(workspace): "<WORKSPACE>"}

        def sanitize(value):
            if isinstance(value, dict):
                if value.get("sessionUpdate") == "agent_thought_chunk":
                    return {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "<omitted>"},
                    }
                result = {}
                for key, item in value.items():
                    if key == "_meta":
                        result[key] = (
                            {"keysOnly": sorted(item)}
                            if isinstance(item, dict)
                            else "<omitted>"
                        )
                    elif key in {"data", "rawInput", "rawOutput"}:
                        result[key] = "<omitted>"
                    elif key in {"sessionId", "cwd", "title"}:
                        result[key] = f"<{key}>"
                    elif key == "message":
                        result[key] = (
                            item
                            if item
                            in {
                                "Authentication required",
                                "Unknown model ID: contract-probe-nonexistent-model",
                            }
                            else "<omitted>"
                        )
                    else:
                        result[key] = sanitize(item)
                return result
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            if isinstance(value, str):
                for original, replacement in replacements.items():
                    value = value.replace(original, replacement)
                return value
            return value

        async def send(message):
            records.append({"direction": "client", "message": sanitize(message)})
            proc.stdin.write((json.dumps(message) + "\n").encode())
            await proc.stdin.drain()

        async def request(method, params, request_id, cancel_session=None):
            await send(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
            if cancel_session:
                await asyncio.sleep(0.2)
                await send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": cancel_session},
                    }
                )
            async with asyncio.timeout(25):
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        raise RuntimeError("ACP EOF")
                    message = json.loads(line)
                    records.append(
                        {"direction": "server", "message": sanitize(message)}
                    )
                    if "method" in message and "id" in message:
                        if message["method"] == "session/request_permission":
                            reject = next(
                                (
                                    o
                                    for o in message["params"].get("options", [])
                                    if o.get("kind") == "reject_once"
                                ),
                                None,
                            )
                            if reject:
                                outcome_value = {
                                    "outcome": "selected",
                                    "optionId": reject["optionId"],
                                }
                            else:
                                await send(
                                    {
                                        "jsonrpc": "2.0",
                                        "method": "session/cancel",
                                        "params": {
                                            "sessionId": message["params"]["sessionId"]
                                        },
                                    }
                                )
                                outcome_value = {"outcome": "cancelled"}
                            reply = {"result": {"outcome": outcome_value}}
                        else:
                            reply = {
                                "error": {"code": -32601, "message": "Unsupported"}
                            }
                        await send({"jsonrpc": "2.0", "id": message["id"], **reply})
                    elif "method" not in message and message.get("id") == request_id:
                        return message

        outcome = "unknown"
        try:
            await request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {
                        "name": "litellm-antigravity-rpc",
                        "version": "0.1.0.dev0",
                    },
                },
                1,
            )
            session = await request(
                "session/new",
                {
                    "cwd": str(workspace),
                    "mcpServers": [],
                },
                2,
            )
            if "error" in session:
                outcome = "session_new_error"
            else:
                sid = session["result"]["sessionId"]
                replacements[sid] = "<SESSION>"
                for option in session["result"].get("configOptions", []):
                    if option.get("category") == "model":
                        await request(
                            "session/set_config_option",
                            {
                                "sessionId": sid,
                                "configId": option["id"],
                                "value": option["currentValue"],
                            },
                            3,
                        )
                        break
                await request(
                    "session/set_config_option",
                    {
                        "sessionId": sid,
                        "configId": "model",
                        "value": "contract-probe-nonexistent-model",
                    },
                    5,
                )
                if prompt_probe:
                    prompt_text = "Reply exactly ACP_CONTRACT_OK. Do not use tools."
                    if cancel_probe:
                        prompt_text = "Write the numbers 1 to 5000 as plain text. Do not use tools."
                    if tool_probe or permission_probe:
                        prompt_text = (
                            "Use a terminal or file-writing tool to create probe-marker.txt "
                            "in the current workspace containing ACP_TOOL_PROBE. "
                            "If tools are unavailable, reply TOOLS_UNAVAILABLE."
                        )
                    await request(
                        "session/prompt",
                        {
                            "sessionId": sid,
                            "prompt": [{"type": "text", "text": prompt_text}],
                        },
                        6,
                        sid if cancel_probe else None,
                    )
                    if usage_probe:
                        await request(
                            "session/prompt",
                            {
                                "sessionId": sid,
                                "prompt": [
                                    {
                                        "type": "text",
                                        "text": "Reply exactly SECOND_OK. Do not use tools.",
                                    }
                                ],
                            },
                            9,
                        )
                await send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": sid},
                    }
                )
                await request("session/close", {"sessionId": sid}, 4)
                if retention_probe:
                    await request(
                        "session/load",
                        {
                            "sessionId": sid,
                            "cwd": str(workspace),
                            "mcpServers": [],
                        },
                        7,
                    )
                    await request("session/close", {"sessionId": sid}, 8)
                outcome = "session_created_idle_cancel_close_attempted"
        except (TimeoutError, OSError, ValueError, RuntimeError, KeyError) as exc:
            outcome = type(exc).__name__
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(proc.pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(killer.wait(), 5)
                elif proc.returncode is None:
                    proc.kill()
                await asyncio.wait_for(proc.wait(), 5)
        return {
            "provenance": f"live agy 1.5.3 on {platform.system()}; sanitized",
            "capturedAt": datetime.now(timezone(timedelta(hours=7))).isoformat(),
            "profile": "cached-login"
            if cached_login
            else "child-env-isolation-attempt",
            "modelPromptSent": any(
                r["message"].get("method") == "session/prompt" for r in records
            ),
            "restrictedAgentRequested": prompt_probe,
            "activeCancelRequested": cancel_probe,
            "toolProbeRequested": tool_probe,
            "absoluteGeminiDirRequested": fresh_state,
            "retentionProbeRequested": retention_probe,
            "canaryProbeRequested": canary_probe,
            "canariesOutsideProfile": outside_canary,
            "twoTurnUsageProbe": usage_probe,
            "permissionProbeRequested": permission_probe,
            "canaryMarkers": sorted(p.name for p in root.glob("*.marker")),
            "markerCreated": (workspace / "probe-marker.txt").exists(),
            "outcome": outcome,
            "records": records,
            "limits": [
                "Only an active cancel probe tests turn cancellation",
                "Model listing does not prove access to every listed model",
                "OS keyring is not isolated by child environment changes",
                "No production process-tree cleanup stress test",
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cached-login", action="store_true")
    parser.add_argument(
        "--prompt-probe",
        action="store_true",
        help="Send a fixed text prompt using a tools: [] test agent",
    )
    parser.add_argument("--cancel-probe", action="store_true")
    parser.add_argument("--tool-probe", action="store_true")
    parser.add_argument("--fresh-state", action="store_true")
    parser.add_argument("--retention-probe", action="store_true")
    parser.add_argument("--canary-probe", action="store_true")
    parser.add_argument("--outside-canary", action="store_true")
    parser.add_argument("--usage-probe", action="store_true")
    parser.add_argument("--permission-probe", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    executable = shutil.which("agy")
    if not executable:
        parser.error("agy is required")
    actual_version = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if actual_version != "1.5.3":
        parser.error(
            "This evidence probe is pinned to agy 1.5.3; review version drift first"
        )
    report = asyncio.run(
        capture(
            executable,
            args.cached_login,
            args.prompt_probe
            or args.cancel_probe
            or args.tool_probe
            or args.canary_probe
            or args.outside_canary
            or args.usage_probe
            or args.permission_probe,
            args.cancel_probe,
            args.tool_probe,
            args.fresh_state
            or args.canary_probe
            or args.outside_canary
            or args.permission_probe,
            args.retention_probe,
            args.canary_probe or args.outside_canary,
            args.outside_canary,
            args.usage_probe,
            args.permission_probe,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"outcome": report["outcome"], "records": len(report["records"])}))


if __name__ == "__main__":
    main()
