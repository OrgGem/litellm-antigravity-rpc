import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_MODE"] = "PRODUCTION"

import httpx  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from test_backend import backend  # noqa: E402

from litellm_antigravity_rpc.app import App  # noqa: E402
from litellm_antigravity_rpc.runtime import Runtime  # noqa: E402

KEY = "fixture-api-key-at-least-16"


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audit = Path(self.temp.name) / "audit"
        self.runtime = Runtime(backend(audit=self.audit, api_key=KEY, timeout=3))
        self.app = App(self.runtime)
        await asyncio.wrap_future(self.runtime.submit(self.runtime.backend.startup()))
        self.app.ready = True
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://fixture"
        )
        self.sdk = AsyncOpenAI(
            api_key=KEY,
            base_url="http://fixture/v1",
            http_client=self.http,
            max_retries=0,
        )

    async def asyncTearDown(self):
        await self.sdk.close()
        await asyncio.to_thread(self.runtime.close)
        self.temp.cleanup()

    async def test_openai_sdk_chat_models_and_stream(self):
        models = await self.sdk.models.list()
        self.assertEqual(models.data[0].id, "test")
        params = {"model": "test", "messages": [{"role": "user", "content": "hello"}]}
        result = await self.sdk.chat.completions.create(**params)
        self.assertEqual(result.choices[0].message.content, "Hello world")
        stream = await self.sdk.chat.completions.create(
            **params, stream=True, stream_options={"include_usage": True}
        )
        chunks = [chunk async for chunk in stream]
        text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        self.assertEqual(text, "Hello world")
        self.assertEqual(chunks[-1].usage.total_tokens, 6)

    async def test_auth_and_strict_validation(self):
        response = await self.http.get("/v1/models")
        self.assertEqual(response.status_code, 401)
        valid = {"model": "test", "messages": [{"role": "user", "content": "x"}]}
        bodies = [
            {**valid, "temperature": 0},
            {**valid, "messages": [{"role": "system", "content": "x"}]},
            {**valid, "messages": [{"role": "user", "content": "/plan x"}]},
            {**valid, "stream": 1},
            {**valid, "model": "missing"},
        ]
        before = self.audit.read_text().count("session/prompt")
        for body in bodies:
            response = await self.http.post(
                "/v1/chat/completions",
                json=body,
                headers={"Authorization": "Bearer " + KEY},
            )
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.audit.read_text().count("session/prompt"), before)

    async def test_disconnect_cancels_backend(self):
        incoming = asyncio.Queue()
        await incoming.put(
            {
                "type": "http.request",
                "body": json.dumps(
                    {
                        "model": "test",
                        "messages": [{"role": "user", "content": "hold"}],
                        "stream": True,
                    }
                ).encode(),
            }
        )
        output = []

        async def send(message):
            output.append(message)

        scope = {
            "type": "http",
            "path": "/v1/chat/completions",
            "method": "POST",
            "headers": [(b"authorization", ("Bearer " + KEY).encode())],
        }
        task = asyncio.create_task(self.app(scope, incoming.get, send))
        for _ in range(100):
            if "session/prompt" in self.audit.read_text():
                break
            await asyncio.sleep(0.02)
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 5)
        self.assertIn("session/cancel", self.audit.read_text())
        self.assertFalse(output)

    async def test_error_after_sse_headers_has_no_done(self):
        runtime = Runtime(backend("late_error", api_key=KEY))
        app = App(runtime)
        app.ready = True
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture"
            ) as http:
                response = await http.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + KEY},
                    json={
                        "model": "test",
                        "messages": [{"role": "user", "content": "x"}],
                        "stream": True,
                    },
                )
            self.assertEqual(response.status_code, 200)
            self.assertIn('"error"', response.text)
            self.assertNotIn("[DONE]", response.text)
            self.assertNotIn("private error", response.text)
        finally:
            await asyncio.to_thread(runtime.close)
