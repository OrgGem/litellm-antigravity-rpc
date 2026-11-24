"""Guarded native driver tests using only a synthetic ACP subprocess."""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from litellm_antigravity_rpc.config import Config, Model
from litellm_antigravity_rpc.native_v2 import (
    CONTRACT_HASH,
    AntigravityNativeDriver,
    DriverError,
)
from litellm_antigravity_rpc.transport import ACPClient

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "agent-rpc" / "v0.1"
SCHEMA = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(
    {"$ref": "#/$defs/AdapterEvent", "$defs": SCHEMA["$defs"]}
)
STUB = str(Path(__file__).parent / "fixtures" / "acp_v2_stub.py")


def context():
    now = time.monotonic()
    return {
        "key_scope_id": "key",
        "key_epoch": "epoch",
        "environment": "fixture",
        "request_id": "request",
        "chain_id": "chain",
        "locator": "a" * 64,
        "session_scope": "b" * 64,
        "profile_id": "fixture",
        "profile_revision": "rev",
        "limits": {
            "inference_seconds": 5,
            "native_deadline": now + 10,
            "chain_deadline": now + 10,
            "tool_wait_seconds": 5,
            "cleanup_seconds": 2,
            "max_handoffs": 2,
        },
    }


def turn_input():
    return {
        "messages": [{"role": "user", "content": "fixture"}],
        "history_digest": "c" * 64,
        "toolset_digest": "d" * 64,
        "model_alias": "agy",
        "resolved_model": "fixture-model",
        "tools": [],
        "round_policy": {"tool_choice": "auto", "parallel_tool_calls": False},
        "reasoning_output": "off",
        "reasoning_effort": None,
    }


async def driver_fixture(tmp_path, scenario="normal"):
    audit = tmp_path / "audit.jsonl"
    config = Config(models={"agy": Model("fixture-model")})

    async def factory(_, on_update):
        client = ACPClient(config, on_update)
        await client.start(
            [sys.executable, STUB, scenario, str(audit)], tmp_path, os.environ.copy()
        )
        return client

    driver = AntigravityNativeDriver._for_protocol_tests(factory)
    session = await driver.open_session(
        context(), {"profile_id": "fixture", "profile_revision": "rev"}
    )
    return driver, session, audit


def methods(audit):
    return (
        [
            json.loads(line)["method"]
            for line in audit.read_text(encoding="utf-8").splitlines()
        ]
        if audit.exists()
        else []
    )


def test_contract_snapshot():
    manifest = json.loads((CONTRACT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_hash"] == CONTRACT_HASH
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest() == expected


async def test_manifest_and_production_gate_before_spawn():
    driver = AntigravityNativeDriver()
    manifest = await driver.capabilities({"profile_id": "fixture"}, "fixture-model")
    assert all(value["state"] == "blocked" for value in manifest["features"].values())
    validator = Draft202012Validator(
        {"$ref": "#/$defs/CapabilityManifest", "$defs": SCHEMA["$defs"]}
    )
    assert validator.is_valid(manifest)
    with pytest.raises(DriverError, match="profile_isolation_unverified"):
        await driver.open_session(
            context(), {"profile_id": "fixture", "profile_revision": "rev"}
        )


async def test_real_fixture_process_text_unknown_usage_and_cleanup(tmp_path):
    driver, session, audit = await driver_fixture(tmp_path)
    turn = await driver.begin_turn(session, turn_input())
    events = [event async for event in driver.events(turn)]
    assert all(VALIDATOR.is_valid(event) for event in events)
    assert [event["sequence"] for event in events] == list(range(len(events)))
    assert {event["invocation_id"] for event in events} == {turn.invocation_id}
    assert [
        event["payload"]["text"] for event in events if event["type"] == "text_delta"
    ] == ["fixture text"]
    assert "PRIVATE_THOUGHT" not in json.dumps(events)
    usage = next(event["payload"] for event in events if event["type"] == "usage")
    assert usage["completeness"] == "unknown" and usage["input_tokens"] is None
    assert events[-1]["type"] == "native_turn_ended"
    await driver.close_session(session, time.monotonic() + 2)
    assert methods(audit) == [
        "initialize",
        "session/new",
        "session/set_config_option",
        "session/prompt",
        "session/close",
    ]


@pytest.mark.parametrize(
    "change", ["tools", "reasoning", "parallel", "token_limit", "history"]
)
async def test_unsupported_request_fails_before_subprocess(tmp_path, change):
    driver, session, audit = await driver_fixture(tmp_path)
    request = turn_input()
    if change == "tools":
        request["tools"] = [
            {"type": "function", "function": {"name": "echo", "parameters": {}}}
        ]
    elif change == "reasoning":
        request["reasoning_output"] = "summary"
    elif change == "parallel":
        request["round_policy"]["parallel_tool_calls"] = True
    elif change == "token_limit":
        request["round_policy"]["max_completion_tokens"] = 10
    else:
        request["messages"].insert(0, {"role": "system", "content": "context"})
    with pytest.raises(DriverError):
        await driver.begin_turn(session, request)
    assert not audit.exists()
    await driver.close_session(session, time.monotonic() + 2)


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("tool", "unexpected_tool_activity"),
        ("early_tool", "invalid_backend_response"),
        ("invalid_usage", "invalid_backend_usage"),
        ("version", "unsupported_cli_version"),
    ],
)
async def test_protocol_failures_are_sanitized_and_never_finish_success(
    tmp_path, scenario, code
):
    driver, session, audit = await driver_fixture(tmp_path, scenario)
    turn = await driver.begin_turn(session, turn_input())
    events = [event async for event in driver.events(turn)]
    assert (
        events[-1]["type"] == "backend_error" and events[-1]["payload"]["code"] == code
    )
    assert not any(event["type"] == "native_turn_ended" for event in events)
    assert "PRIVATE_THOUGHT" not in json.dumps(events)
    if scenario in ("early_tool", "version"):
        assert "session/prompt" not in methods(audit)


async def test_cancel_hold_sends_native_cancel_and_reaps_process(tmp_path):
    driver, session, audit = await driver_fixture(tmp_path, "hold")
    turn = await driver.begin_turn(session, turn_input())
    iterator = driver.events(turn)
    event = await asyncio.wait_for(anext(iterator), 5)
    assert event["type"] == "text_delta"
    await iterator.aclose()
    await driver.cancel(turn, "client_disconnect", time.monotonic() + 4)
    assert "session/cancel" in methods(audit)
    assert session.client is None and session.closed
    await driver.close_session(session, time.monotonic() + 2)


async def test_reuse_and_foreign_driver_rejected(tmp_path):
    driver, session, _ = await driver_fixture(tmp_path)
    other, _, _ = await driver_fixture(tmp_path)
    with pytest.raises(DriverError, match="invalid_session"):
        await other.begin_turn(session, turn_input())
    turn = await driver.begin_turn(session, turn_input())
    assert [event async for event in driver.events(turn)][-1][
        "type"
    ] == "native_turn_ended"
    with pytest.raises(DriverError, match="session_closed"):
        await driver.begin_turn(session, turn_input())


async def test_consumer_can_detach_and_reattach_while_native_turn_continues(tmp_path):
    driver, session, _ = await driver_fixture(tmp_path)
    turn = await driver.begin_turn(session, turn_input())
    iterator = driver.events(turn)
    first = await asyncio.wait_for(anext(iterator), 5)
    assert first["type"] == "text_delta"
    await iterator.aclose()
    tail = [event async for event in driver.events(turn)]
    assert tail[-1]["type"] == "native_turn_ended"
    assert [event async for event in driver.events(turn)] == []


async def test_finished_owner_does_not_report_closed_before_transport_cleanup(tmp_path):
    gate = asyncio.Event()
    config = Config(models={"agy": Model("fixture-model")})
    audit = tmp_path / "cleanup.jsonl"

    async def factory(_, on_update):
        client = ACPClient(config, on_update)
        await client.start(
            [sys.executable, STUB, "normal", str(audit)], tmp_path, os.environ.copy()
        )
        original_close = client.close

        async def delayed_close():
            await gate.wait()
            await original_close()

        client.close = delayed_close
        return client

    driver = AntigravityNativeDriver._for_protocol_tests(factory)
    session = await driver.open_session(
        context(), {"profile_id": "fixture", "profile_revision": "rev"}
    )
    turn = await driver.begin_turn(session, turn_input())
    iterator = driver.events(turn)
    assert (await asyncio.wait_for(anext(iterator), 5))["type"] == "text_delta"
    assert (await asyncio.wait_for(anext(iterator), 5))["type"] == "usage"
    assert turn.finished and session.active is turn
    with pytest.raises(DriverError, match="cleanup_timeout"):
        await driver.close_session(session, time.monotonic() + 0.05)
    assert session.active is turn
    gate.set()
    assert (await asyncio.wait_for(anext(iterator), 5))["type"] == "native_turn_ended"
    await iterator.aclose()
    await driver.close_session(session, time.monotonic() + 2)


async def test_cleanup_error_does_not_emit_success(tmp_path):
    config = Config(models={"agy": Model("fixture-model")})
    audit = tmp_path / "cleanup_error.jsonl"

    async def factory(_, on_update):
        client = ACPClient(config, on_update)
        await client.start(
            [sys.executable, STUB, "normal", str(audit)], tmp_path, os.environ.copy()
        )
        original_close = client.close

        async def failing_close():
            await original_close()
            raise OSError("private error")

        client.close = failing_close
        return client

    driver = AntigravityNativeDriver._for_protocol_tests(factory)
    session = await driver.open_session(
        context(), {"profile_id": "fixture", "profile_revision": "rev"}
    )

    class Profile:
        workspace = tmp_path
        closed = False

        def close(self):
            self.closed = True

    profile = Profile()
    session.profile = profile
    turn = await driver.begin_turn(session, turn_input())
    events = [event async for event in driver.events(turn)]
    assert events[-1]["type"] == "backend_error"
    assert events[-1]["payload"]["code"] == "cleanup_failed"
    assert not any(event["type"] == "native_turn_ended" for event in events)
    assert "private error" not in json.dumps(events)
    assert not profile.closed
    with pytest.raises(DriverError, match="cleanup_failed"):
        await driver.close_session(session, time.monotonic() + 2)


async def test_cancel_before_owner_starts_finishes_without_spawning(tmp_path):
    driver, session, audit = await driver_fixture(tmp_path)
    turn = await driver.begin_turn(session, turn_input())
    await driver.cancel(turn, "transport_closed", time.monotonic() + 2)
    assert await asyncio.wait_for(anext(driver.events(turn), None), 1) is None
    assert turn.producer_ended and session.active is None and not audit.exists()
    await driver.close_session(session, time.monotonic() + 2)


async def test_inference_budget_bounds_blocked_startup_even_with_long_wall_deadline():
    entered = asyncio.Event()

    async def blocked_factory(*_):
        entered.set()
        await asyncio.Event().wait()

    driver = AntigravityNativeDriver._for_protocol_tests(blocked_factory)
    execution = context()
    execution["limits"]["inference_seconds"] = 0.02
    session = await driver.open_session(
        execution, {"profile_id": "fixture", "profile_revision": "rev"}
    )
    turn = await driver.begin_turn(session, turn_input())
    first = await asyncio.wait_for(anext(driver.events(turn)), 1)
    assert entered.is_set()
    assert first["type"] == "backend_error"
    assert first["payload"] == {"code": "deadline_exceeded", "status": 504}
    await driver.close_session(session, time.monotonic() + 2)
