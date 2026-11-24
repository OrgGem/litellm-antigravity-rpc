import asyncio
import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from litellm_antigravity_rpc.backend import ACPBackend
from litellm_antigravity_rpc.config import Config, Model
from litellm_antigravity_rpc.contracts import Request
from litellm_antigravity_rpc.errors import GatewayError
from litellm_antigravity_rpc.profile import Profile

FAKE = str(Path(__file__).with_name("fake_acp.py"))


def backend(scenario="normal", audit=None, **kwargs):
    config = Config(models={"test": Model("fixture-model")}, **kwargs)
    command = [sys.executable, "-u", FAKE, scenario]
    if audit:
        command.append(str(audit))
    return ACPBackend(config, command_factory=lambda profile: command)


def is_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return (
                bool(api.GetExitCodeProcess(handle, ctypes.byref(code)))
                and code.value == 259
            )
        finally:
            api.CloseHandle(handle)
    try:
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().split()[2] == "Z":
            return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_usage_and_separate_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            instance = backend(audit=audit)
            await instance.startup()
            for _ in range(2):
                events = [
                    e async for e in instance.stream(Request("hello", "r1", "test"))
                ]
                self.assertEqual("".join(e.text for e in events), "Hello world")
                self.assertTrue(events[-1].done)
                self.assertEqual(events[-1].usage.input_tokens, 4)
            starts = [
                json.loads(line)["pid"]
                for line in audit.read_text().splitlines()
                if json.loads(line).get("method") == "initialize"
            ]
            self.assertEqual(len(set(starts)), 3)

    async def test_protocol_failures_and_auth(self):
        expected = {
            "malformed": "invalid_frame",
            "oversize": "invalid_frame",
            "mismatched": "unexpected_response_id",
            "eof": "upstream_eof",
            "auth": "upstream_authentication_required",
            "tool": "unexpected_tool_activity",
        }
        for scenario, code in expected.items():
            with (
                self.subTest(scenario=scenario),
                self.assertRaises(GatewayError) as caught,
            ):
                _ = [
                    e async for e in backend(scenario).stream(Request("x", "r", "test"))
                ]
            self.assertEqual(caught.exception.code, code)

    async def test_late_error_is_not_success(self):
        events = []
        with self.assertRaises(GatewayError):
            async for event in backend("late_error").stream(Request("x", "r", "test")):
                events.append(event)
        self.assertTrue(any(e.text for e in events))
        self.assertFalse(any(e.done for e in events))

    async def test_permission_is_denied_and_request_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            with self.assertRaises(GatewayError) as caught:
                _ = [
                    e
                    async for e in backend("permission", audit).stream(
                        Request("x", "r", "test")
                    )
                ]
            self.assertEqual(caught.exception.code, "unexpected_tool_request")
            replies = [
                json.loads(line)["permission_reply"]
                for line in audit.read_text().splitlines()
                if "permission_reply" in json.loads(line)
            ]
            self.assertEqual(replies[0]["id"], "1")
            self.assertEqual(
                replies[0]["result"]["outcome"],
                {"outcome": "selected", "optionId": "deny"},
            )

    async def test_timeout_cancels_and_kills_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            with self.assertRaises(GatewayError) as caught:
                _ = [
                    e
                    async for e in backend("child", audit, timeout=0.6).stream(
                        Request("x", "r", "test")
                    )
                ]
            self.assertEqual(caught.exception.status, 504)
            records = [json.loads(line) for line in audit.read_text().splitlines()]
            self.assertTrue(any(r.get("method") == "session/cancel" for r in records))
            child = next(r["child"] for r in records if "child" in r)
            for _ in range(20):
                if not is_alive(child):
                    break
                await asyncio.sleep(0.05)
            self.assertFalse(is_alive(child))

    async def test_queue_wait_is_inside_deadline(self):
        instance = backend(timeout=0.1, concurrency=1)
        await instance.semaphore.acquire()
        try:
            with self.assertRaises(GatewayError) as caught:
                _ = [e async for e in instance.stream(Request("x", "r", "test"))]
            self.assertEqual(caught.exception.code, "deadline_exceeded")
            self.assertEqual(instance.pending, 0)
        finally:
            instance.semaphore.release()

    async def test_slow_consumer_fails_without_dropping_to_success(self):
        instance = backend("burst", queue_size=2)
        with self.assertRaises(GatewayError) as caught:
            async with contextlib.aclosing(
                instance.stream(Request("x", "r", "test"))
            ) as events:
                async for _ in events:
                    await asyncio.sleep(0.1)
        self.assertEqual(caught.exception.code, "slow_consumer")

    async def test_unknown_alias_does_not_spawn(self):
        with self.assertRaises(GatewayError) as caught:
            _ = [e async for e in backend().stream(Request("x", "r", "missing"))]
        self.assertEqual(caught.exception.code, "unknown_model")

    async def test_early_generator_close_finishes_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            instance = backend("after_text_hold", audit)
            async with contextlib.aclosing(
                instance.stream(Request("x", "r", "test"))
            ) as events:
                self.assertEqual((await anext(events)).text, "Hello ")
            records = [json.loads(line) for line in audit.read_text().splitlines()]
            self.assertTrue(any(r.get("method") == "session/cancel" for r in records))
            self.assertFalse(is_alive(records[0]["pid"]))
            self.assertEqual(instance.pending, 0)

    def test_profile_rejects_unexpected_config(self):
        profile = Profile()
        try:
            command = profile.command(sys.executable)
            self.assertIn(str(profile.gemini), command)
            (profile.workspace / ".agents" / "hooks.json").write_text("{}")
            with self.assertRaises(ValueError):
                profile.command(sys.executable)
        finally:
            profile.close()
