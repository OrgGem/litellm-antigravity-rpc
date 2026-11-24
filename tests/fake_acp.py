"""Controllable real subprocess fixture. Never calls an external model."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

scenario = sys.argv[1]
audit = Path(sys.argv[2]) if len(sys.argv) > 2 else None
lock = threading.Lock()
prompt_id = None
sid = "test-session"
options = [
    {
        "id": "model",
        "category": "model",
        "type": "select",
        "currentValue": "fixture-model",
        "options": [{"value": "fixture-model", "name": "Fixture"}],
    }
]


def record(value):
    if audit:
        with audit.open("a", encoding="utf-8") as file:
            file.write(json.dumps(value) + "\n")


def send(value):
    with lock:
        print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


def update(text, kind="agent_message_chunk"):
    send(
        {
            "method": "session/update",
            "params": {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": kind,
                    "content": {"type": "text", "text": text},
                },
            },
        }
    )


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method is None:
        record({"permission_reply": message})
        continue
    record({"method": method, "pid": os.getpid()})
    if method == "initialize":
        if scenario == "hold_initialize":
            continue
        if scenario == "malformed":
            print("not JSON", flush=True)
            continue
        if scenario == "oversize":
            print("x" * (1024 * 1024 + 10), flush=True)
            continue
        if scenario == "mismatched":
            send({"id": 999, "result": {}})
            continue
        if scenario == "eof":
            break
        send(
            {
                "id": request_id,
                "result": {"protocolVersion": 1, "agentCapabilities": {}},
            }
        )
    elif method == "session/new":
        if scenario == "auth":
            send(
                {
                    "id": request_id,
                    "error": {"code": -32000, "message": "private credential detail"},
                }
            )
        else:
            send(
                {
                    "id": request_id,
                    "result": {"sessionId": sid, "configOptions": options},
                }
            )
    elif method == "session/set_config_option":
        send({"id": request_id, "result": {"configOptions": options}})
    elif method == "session/prompt":
        prompt_id = request_id
        text = message["params"]["prompt"][0]["text"]
        record({"prompt_text": text})
        if scenario == "child" or text == "child":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            record({"child": child.pid})
        if scenario in {"hold", "child"} or text in {"hold", "child"}:
            continue
        if scenario == "permission":
            send(
                {
                    "id": "1",
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": sid,
                        "options": [{"kind": "reject_once", "optionId": "deny"}],
                        "toolCall": {"toolCallId": "t1"},
                    },
                }
            )
            continue
        if scenario == "tool":
            send(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": sid,
                        "update": {"sessionUpdate": "tool_call", "toolCallId": "t1"},
                    },
                }
            )
            continue
        update("secret thought", "agent_thought_chunk")
        if scenario == "after_text_hold" or text == "after_text_hold":
            update("Hello ")
            continue
        if scenario == "paced":
            for _ in range(20):
                update("x")
                time.sleep(0.02)
            continue
        if scenario == "burst":
            for _ in range(1000):
                update("x")
        else:
            update("Hello ")
            time.sleep(0.08)
            update("world")
        if scenario == "late_error":
            time.sleep(0.1)
            send(
                {
                    "id": request_id,
                    "error": {"code": -32603, "message": "private error text"},
                }
            )
        elif scenario == "rich_usage" or "fixture-rich-usage" in text:
            send(
                {
                    "id": request_id,
                    "result": {
                        "stopReason": "end_turn",
                        "usage": {
                            "inputTokens": 10,
                            "outputTokens": 5,
                            "cachedReadTokens": 4,
                            "cachedWriteTokens": 2,
                            "thoughtTokens": 3,
                            "totalTokens": 15,
                        },
                    },
                }
            )
        elif scenario == "max_tokens" or "fixture-max-tokens" in text:
            send(
                {
                    "id": request_id,
                    "result": {
                        "stopReason": "max_tokens",
                        "usage": {
                            "inputTokens": 6,
                            "outputTokens": 12,
                            "cachedWriteTokens": 2,
                        },
                    },
                }
            )
        elif scenario == "no_usage" or "fixture-no-usage" in text:
            send(
                {
                    "id": request_id,
                    "result": {
                        "stopReason": "end_turn",
                        "usage": None,
                    },
                }
            )
        else:
            send(
                {
                    "id": request_id,
                    "result": {
                        "stopReason": "end_turn",
                        "usage": {"inputTokens": 4, "outputTokens": 2},
                    },
                }
            )
    elif method == "session/cancel":
        if prompt_id is not None:
            send({"id": prompt_id, "result": {"stopReason": "cancelled"}})
            prompt_id = None
    elif method == "session/close":
        send({"id": request_id, "result": {}})
