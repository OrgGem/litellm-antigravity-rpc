"""In-memory CustomLLM compatibility spike; no ACP backend or model calls."""

import asyncio
import json
import os
from importlib.metadata import version

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_MODE"] = "PRODUCTION"

import litellm  # noqa: E402
from litellm import CustomLLM, ModelResponse  # noqa: E402


class FixtureLLM(CustomLLM):
    def completion(self, *args, **kwargs):
        return ModelResponse(
            model="contract-fixture",
            choices=[
                {
                    "message": {"role": "assistant", "content": "ACP OK"},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            usage=None,
        )

    async def acompletion(self, *args, **kwargs):
        return self.completion(*args, **kwargs)

    def streaming(self, *args, **kwargs):
        for text, done in [("ACP ", False), ("OK", False), ("", True)]:
            yield {
                "text": text,
                "is_finished": done,
                "finish_reason": "stop" if done else "",
                "usage": None,
                "index": 0,
            }

    async def astreaming(self, *args, **kwargs):
        for chunk in self.streaming(*args, **kwargs):
            yield chunk


def check_stream(chunks):
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "ACP OK", text
    assert chunks[-1].choices[0].finish_reason == "stop"


async def main():
    litellm.telemetry = False
    litellm.suppress_debug_info = True
    litellm.custom_provider_map = [
        {"provider": "contract-fixture", "custom_handler": FixtureLLM()}
    ]
    params = {
        "model": "contract-fixture/test",
        "messages": [{"role": "user", "content": "fixture"}],
        "num_retries": 0,
    }
    sync = litellm.completion(**params)
    assert sync.choices[0].message.content == "ACP OK"
    async_result = await litellm.acompletion(**params)
    assert async_result.choices[0].message.content == "ACP OK"
    check_stream(list(litellm.completion(**params, stream=True)))
    stream = await litellm.acompletion(**params, stream=True)
    check_stream([chunk async for chunk in stream])
    print(
        json.dumps(
            {
                "litellm": version("litellm"),
                "openai": version("openai"),
                "checks": ["sync", "async", "sync-stream", "async-stream"],
                "result": "pass",
                "modelCalls": 0,
                "unknownUsageAfterLiteLLM": sync.model_dump().get("usage"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
