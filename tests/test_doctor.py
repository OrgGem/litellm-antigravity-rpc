import json
import sys
import unittest
from pathlib import Path

from litellm_antigravity_rpc.doctor import ProbeError, probe


class DoctorTests(unittest.IsolatedAsyncioTestCase):
    async def replay(self, message):
        script = (
            "import sys; sys.stdin.readline(); print("
            + repr(json.dumps(message))
            + ", flush=True)"
        )
        return await probe([sys.executable, "-c", script])

    async def test_live_initialize_fixture(self):
        path = Path(__file__).parent / "fixtures/acp/live-isolated.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        result = await self.replay(fixture["records"][1]["message"])
        self.assertEqual(result["agentInfo"]["version"], "1.5.3")
        self.assertIn("oauth-personal", result["authMethodIds"])
        self.assertFalse(result["modelAccessVerified"])

    async def test_invalid_auth_methods_become_protocol_error(self):
        for methods in (None, 1, {}, [None], [{"id": 1}]):
            with self.subTest(methods=methods), self.assertRaises(ProbeError):
                await self.replay(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "protocolVersion": 1,
                            "agentCapabilities": {},
                            "authMethods": methods,
                        },
                    }
                )

    async def test_boolean_id_is_not_request_one(self):
        with self.assertRaises(ProbeError):
            await self.replay(
                {
                    "jsonrpc": "2.0",
                    "id": True,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {},
                    },
                }
            )

    async def test_nonfinite_timeout_is_rejected_before_spawn(self):
        for timeout in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                await probe(["not-an-executable"], timeout=timeout)

    async def test_handshake_only(self):
        script = """
import json, sys
request = json.loads(sys.stdin.readline())
assert request["method"] == "initialize"
assert request["params"]["clientCapabilities"] == {}
print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {
    "protocolVersion": 1, "agentCapabilities": {}, "authMethods": []
}}), flush=True)
assert sys.stdin.read() == ""
"""
        result = await probe([sys.executable, "-c", script])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["authenticationVerified"])

    async def test_invalid_stdout(self):
        with self.assertRaises(ProbeError):
            await probe([sys.executable, "-c", "print('not JSON', flush=True)"])

    async def test_remote_error_is_not_exposed(self):
        script = """
print('{"jsonrpc":"2.0","id":1,"error":{"message":"private detail"}}', flush=True)
"""
        with self.assertRaises(ProbeError) as caught:
            await probe([sys.executable, "-c", script])
        self.assertNotIn("private detail", str(caught.exception))

    async def test_timeout_closes_process(self):
        with self.assertRaises(TimeoutError):
            await probe(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1
            )
