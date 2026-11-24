import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_MODE"] = "PRODUCTION"

import httpx  # noqa: E402
import yaml  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from test_backend import backend  # noqa: E402

import litellm_antigravity_rpc  # noqa: E402
from litellm_antigravity_rpc.app import App  # noqa: E402
from litellm_antigravity_rpc.backend import parse_usage  # noqa: E402
from litellm_antigravity_rpc.config import (  # noqa: E402
    Config,
    is_supported_cli_version,
)
from litellm_antigravity_rpc.errors import GatewayError  # noqa: E402
from litellm_antigravity_rpc.provider import (  # noqa: E402
    AntigravityLLM,
    ProviderError,
    antigravity_rpc_provider,
)
from litellm_antigravity_rpc.responses_context import (  # noqa: E402
    responses_request_context,
)
from litellm_antigravity_rpc.responses_guard import (  # noqa: E402
    antigravity_rpc_responses_guard,
)
from litellm_antigravity_rpc.runtime import Runtime  # noqa: E402

KEY = "fixture-api-key-at-least-16"
REPO_ROOT = Path(__file__).resolve().parents[1]


class ConfigAndVersionTests(unittest.TestCase):
    def test_supported_cli_versions(self):
        for ok in ("1.5.3", "1.5.6", "1.5.9", "agy 1.5.9", "1.5.12-rc.1"):
            self.assertTrue(is_supported_cli_version(ok), ok)
        for bad in ("1.5.2", "1.4.9", "2.0.0", "", None, "invalid"):
            self.assertFalse(is_supported_cli_version(bad), bad)

    def test_config_from_env_and_exports(self):
        self.assertIsInstance(antigravity_rpc_provider, AntigravityLLM)
        self.assertIn("antigravity_rpc_provider", litellm_antigravity_rpc.__all__)
        with mock.patch.dict(
            os.environ,
            {
                "ANTIGRAVITY_MODEL_MAP": '{"custom":{"model":"gemini-4-carbon","thought_level":"high"},"default-null":null}',
                "ANTIGRAVITY_RPC_ALLOW_PROMPT_FLATTENING": "true",
                "ANTIGRAVITY_RPC_LIMITS": '{"concurrency":4,"max_pending":20}',
                "LITELLM_MASTER_KEY": KEY,
            },
            clear=False,
        ):
            cfg = Config.from_env()
            self.assertEqual(set(cfg.models), {"custom", "default-null"})
            self.assertIsNone(cfg.models["default-null"].model)
            self.assertEqual(cfg.models["custom"].thought_level, "high")
            self.assertTrue(cfg.flatten)
            self.assertEqual(cfg.concurrency, 4)
            self.assertEqual(cfg.max_pending, 20)
            self.assertEqual(cfg.api_key, KEY)

    def test_usage_details_and_validation(self):
        parsed = parse_usage(
            {
                "inputTokens": 20,
                "outputTokens": 10,
                "cachedReadTokens": 5,
                "cachedWriteTokens": 3,
                "thoughtTokens": 4,
                "totalTokens": 30,
            }
        )
        self.assertEqual(parsed.cached_tokens, 5)
        self.assertEqual(parsed.cache_write_tokens, 3)
        self.assertEqual(parsed.reasoning_tokens, 4)
        with self.assertRaises(GatewayError):
            parse_usage({"inputTokens": 2, "outputTokens": 2, "cachedReadTokens": 5})
        with self.assertRaises(GatewayError):
            parse_usage({"inputTokens": 2, "outputTokens": 2, "thoughtTokens": 5})

    def test_example_proxy_config_and_shim(self):
        cfg = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text("utf-8"))
        self.assertTrue(
            cfg["model_list"][0]["litellm_params"]["use_chat_completions_api"]
        )
        spec = importlib.util.spec_from_file_location(
            "agy_proxy_handler", REPO_ROOT / "proxy_handler.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(module.antigravity_rpc_provider, antigravity_rpc_provider)
        self.assertIs(
            module.antigravity_rpc_responses_guard, antigravity_rpc_responses_guard
        )


class ResponsesAndFlatteningApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audit = Path(self.temp.name) / "audit"
        self.runtime = Runtime(
            backend(audit=self.audit, api_key=KEY, timeout=4, flatten=True)
        )
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

    def prompt_texts(self):
        return [
            json.loads(line)["prompt_text"]
            for line in self.audit.read_text(encoding="utf-8").splitlines()
            if "prompt_text" in json.loads(line)
        ]

    async def test_chat_flattening_content_blocks_and_rich_usage(self):
        chat = await self.sdk.chat.completions.create(
            model="test",
            messages=[
                {"role": "system", "content": "System guidance"},
                {"role": "developer", "content": "Developer guidance"},
                {"role": "user", "content": [{"type": "text", "text": "Turn 1"}]},
                {"role": "assistant", "content": "Reply 1"},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "fixture-rich-usage"}],
                },
            ],
        )
        self.assertEqual(chat.choices[0].message.content, "Hello world")
        self.assertEqual(chat.usage.prompt_tokens, 10)
        self.assertEqual(chat.usage.completion_tokens, 5)
        self.assertEqual(chat.usage.prompt_tokens_details.cached_tokens, 4)
        self.assertEqual(
            chat.usage.model_dump()["prompt_tokens_details"]["cache_write_tokens"], 2
        )
        self.assertEqual(chat.usage.completion_tokens_details.reasoning_tokens, 3)
        sent = self.prompt_texts()[-1]
        self.assertIn("System guidance", sent)
        self.assertIn("Developer guidance", sent)
        self.assertIn("fixture-rich-usage", sent)

        unknown = await self.http.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + KEY},
            json={
                "model": "test",
                "messages": [{"role": "user", "content": "fixture-no-usage"}],
            },
        )
        self.assertEqual(unknown.status_code, 200)
        self.assertNotIn("usage", unknown.json())

    async def test_responses_sdk_sync_stream_phases_and_usage(self):
        res = await self.sdk.responses.create(
            model="test",
            instructions="Respond concisely.",
            input="fixture-rich-usage",
            store=False,
        )
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.output_text, "Hello world")
        dumped = res.model_dump()
        self.assertEqual(dumped["usage"]["input_tokens"], 10)
        self.assertEqual(dumped["usage"]["input_tokens_details"]["cached_tokens"], 4)
        self.assertEqual(
            dumped["usage"]["input_tokens_details"]["cache_write_tokens"], 2
        )
        self.assertEqual(
            dumped["usage"]["output_tokens_details"]["reasoning_tokens"], 3
        )
        self.assertIn("Respond concisely.", self.prompt_texts()[-1])

        assistant_item = res.output[0].model_dump()
        followup = await self.sdk.responses.create(
            model="test",
            input=[
                assistant_item,
                {
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "thinking note"}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "fixture-no-usage"}],
                },
            ],
            store=False,
        )
        self.assertEqual(followup.status, "completed")
        self.assertIsNone(followup.usage)
        self.assertIn('"phase": "commentary"', self.prompt_texts()[-1])

        stream = await self.sdk.responses.create(
            model="test",
            input="fixture-rich-usage",
            store=False,
            stream=True,
        )
        events = [event async for event in stream]
        types = [event.type for event in events]
        self.assertEqual(types[0], "response.created")
        self.assertEqual(types[-1], "response.completed")
        deltas = [e.delta for e in events if e.type == "response.output_text.delta"]
        self.assertEqual("".join(deltas), "Hello world")
        self.assertEqual(events[-1].response.usage.input_tokens, 10)

    async def test_responses_rejects_unsupported_and_handles_stream_failure(self):
        before = len(self.prompt_texts())
        for bad in (
            {"store": True},
            {"previous_response_id": "resp_1"},
            {"tools": [{"type": "function", "name": "f", "parameters": {}}]},
            {"background": True},
            {"temperature": 0.2},
            {"input": [{"role": "user", "content": []}]},
        ):
            r = await self.http.post(
                "/v1/responses",
                headers={"Authorization": "Bearer " + KEY},
                json={"model": "test", "input": "hello", **bad},
            )
            self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(len(self.prompt_texts()), before)

        err_runtime = Runtime(backend("late_error", api_key=KEY))
        err_app = App(err_runtime)
        err_app.ready = True
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=err_app), base_url="http://fixture"
            ) as client:
                r = await client.post(
                    "/v1/responses",
                    headers={"Authorization": "Bearer " + KEY},
                    json={"model": "test", "input": "x", "stream": True},
                )
            self.assertEqual(r.status_code, 200)
            self.assertIn("event: response.failed", r.text)
            self.assertNotIn("event: response.completed", r.text)
        finally:
            await asyncio.to_thread(err_runtime.close)

    async def test_responses_guard_pre_and_post_hooks(self):
        other = {"model": "openai/gpt-4o", "input": "hi"}
        self.assertIs(
            await antigravity_rpc_responses_guard.async_pre_call_hook(
                {}, None, other, "aresponses"
            ),
            other,
        )
        old_runtime = antigravity_rpc_provider.runtime
        antigravity_rpc_provider.runtime = self.runtime
        try:
            data = {
                "model": "antigravity_rpc/test",
                "input": "fixture-rich-usage",
                "instructions": "brief",
                "tools": [],
                "proxy_server_request": {
                    "body": {
                        "model": "test",
                        "input": "fixture-rich-usage",
                        "instructions": "brief",
                        "tools": [],
                    }
                },
            }
            out = await antigravity_rpc_responses_guard.async_pre_call_hook(
                {}, None, data, "aresponses"
            )
            self.assertIs(out["store"], False)
            self.assertNotIn("tools", out)
            ctx = responses_request_context.get()
            self.assertIsNotNone(ctx)
            await antigravity_rpc_provider.acompletion(
                "antigravity_rpc/test",
                [
                    {"role": "system", "content": "brief"},
                    {"role": "user", "content": "fixture-rich-usage"},
                ],
            )
            self.assertTrue(ctx.usage_known)
            synthetic = {
                "object": "response",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {},
                    "output_tokens_details": {},
                },
            }
            await antigravity_rpc_responses_guard.async_post_call_success_hook(
                {}, {}, synthetic
            )
            self.assertEqual(
                synthetic["usage"]["input_tokens_details"]["cache_write_tokens"], 2
            )
            with self.assertRaises(ProviderError):
                await antigravity_rpc_responses_guard.async_pre_call_hook(
                    {},
                    None,
                    {
                        "model": "antigravity_rpc/test",
                        "input": "x",
                        "store": True,
                        "proxy_server_request": {
                            "body": {"model": "test", "input": "x", "store": True}
                        },
                    },
                    "aresponses",
                )
        finally:
            responses_request_context.set(None)
            antigravity_rpc_provider.runtime = old_runtime

    async def test_responses_max_tokens_reports_incomplete_json_and_sse(self):
        chat = await self.sdk.chat.completions.create(
            model="test",
            messages=[{"role": "user", "content": "fixture-max-tokens"}],
        )
        self.assertEqual(chat.choices[0].finish_reason, "length")

        res = await self.sdk.responses.create(
            model="test",
            input="fixture-max-tokens",
            store=False,
        )
        self.assertEqual(res.status, "incomplete")
        self.assertEqual(res.incomplete_details.reason, "max_output_tokens")
        self.assertEqual(res.output[0].status, "incomplete")

        stream = await self.sdk.responses.create(
            model="test",
            input="fixture-max-tokens",
            store=False,
            stream=True,
        )
        events = [event async for event in stream]
        self.assertEqual(events[-1].type, "response.incomplete")
        self.assertEqual(events[-1].response.status, "incomplete")
        self.assertEqual(
            events[-1].response.incomplete_details.reason, "max_output_tokens"
        )
        self.assertEqual(events[-1].response.output[0].status, "incomplete")

