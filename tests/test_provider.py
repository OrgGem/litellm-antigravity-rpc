import asyncio
import os
import unittest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_MODE"] = "PRODUCTION"

import litellm  # noqa: E402
from test_backend import backend  # noqa: E402

from litellm_antigravity_rpc.provider import AntigravityLLM  # noqa: E402
from litellm_antigravity_rpc.runtime import Runtime  # noqa: E402


class ProviderTests(unittest.TestCase):
    def test_litellm_four_routes(self):
        runtime = Runtime(backend())
        handler = AntigravityLLM(runtime)
        old = litellm.custom_provider_map
        litellm.custom_provider_map = [
            {"provider": "antigravity-test", "custom_handler": handler}
        ]
        params = {
            "model": "antigravity-test/test",
            "messages": [{"role": "user", "content": "hello"}],
            "num_retries": 0,
        }
        try:
            self.assertEqual(
                litellm.completion(**params).choices[0].message.content, "Hello world"
            )
            chunks = list(litellm.completion(**params, stream=True))
            self.assertEqual(
                "".join(c.choices[0].delta.content or "" for c in chunks), "Hello world"
            )

            async def check():
                result = await litellm.acompletion(**params)
                self.assertEqual(result.choices[0].message.content, "Hello world")
                stream = await litellm.acompletion(**params, stream=True)
                chunks = [c async for c in stream]
                self.assertEqual(
                    "".join(c.choices[0].delta.content or "" for c in chunks),
                    "Hello world",
                )

            asyncio.run(check())
        finally:
            litellm.custom_provider_map = old
            runtime.close()
