"""Synthetic ACP stdio process. It never loads or invokes an actual CLI/model."""

import json
import sys
from pathlib import Path

scenario = sys.argv[1]
audit = Path(sys.argv[2])
sid = "private-session"
prompt_id = None
options = [
    {
        "id": "model",
        "category": "model",
        "type": "select",
        "currentValue": "fixture-model",
        "options": [{"value": "fixture-model", "name": "Fixture"}],
    }
]


def send(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def update(kind, **fields):
    send(
        {
            "method": "session/update",
            "params": {"sessionId": sid, "update": {"sessionUpdate": kind, **fields}},
        }
    )


for line in sys.stdin:
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"method": method}) + "\n")
    if method == "initialize":
        version = "1.5.9" if scenario == "version" else "1.5.3"
        send(
            {
                "id": request_id,
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {},
                    "agentInfo": {"version": version},
                },
            }
        )
    elif method == "session/new":
        if scenario == "early_tool":
            update("tool_call", toolCallId="private-call")
        send({"id": request_id, "result": {"sessionId": sid, "configOptions": options}})
    elif method == "session/set_config_option":
        send({"id": request_id, "result": {"configOptions": options}})
    elif method == "session/prompt":
        prompt_id = request_id
        update(
            "agent_thought_chunk", content={"type": "text", "text": "PRIVATE_THOUGHT"}
        )
        if scenario == "tool":
            update("tool_call", toolCallId="private-call")
            continue
        update("agent_message_chunk", content={"type": "text", "text": "fixture text"})
        if scenario == "hold":
            continue
        usage = {"inputTokens": 10, "outputTokens": 2}
        if scenario == "invalid_usage":
            usage["outputTokens"] = -1
        send({"id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})
    elif method == "session/cancel":
        if prompt_id is not None:
            send({"id": prompt_id, "result": {"stopReason": "cancelled"}})
            prompt_id = None
    elif method == "session/close":
        send({"id": request_id, "result": {}})
