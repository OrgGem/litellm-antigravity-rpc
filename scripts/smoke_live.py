"""Opt-in live acceptance: starts a temporary localhost server and uses CLI quota."""

import asyncio
import json
import os
import secrets
import socket

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_MODE"] = "PRODUCTION"

import uvicorn  # noqa: E402
from openai import AsyncOpenAI, BadRequestError  # noqa: E402

from litellm_antigravity_rpc.app import App  # noqa: E402
from litellm_antigravity_rpc.backend import ACPBackend  # noqa: E402
from litellm_antigravity_rpc.config import Config, load_models  # noqa: E402
from litellm_antigravity_rpc.runtime import Runtime  # noqa: E402


async def main():
    config = Config(
        models=load_models("models.example.json"),
        api_key=secrets.token_hex(24),
        timeout=60,
    )
    runtime = Runtime(ACPBackend(config))
    app = App(runtime)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, access_log=False, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    results = {}
    try:
        async with asyncio.timeout(70):
            while not server.started:
                if task.done():
                    raise RuntimeError("Server failed readiness")
                await asyncio.sleep(0.05)
        async with AsyncOpenAI(
            api_key=config.api_key,
            base_url=f"http://127.0.0.1:{port}/v1",
            max_retries=0,
            timeout=70,
        ) as client:
            results["models"] = [m.id for m in (await client.models.list()).data]
            response = await client.chat.completions.create(
                model="antigravity",
                messages=[
                    {
                        "role": "user",
                        "content": "Reply exactly ACP_BACKEND_OK. Do not use tools.",
                    }
                ],
            )
            assert response.choices[0].message.content.strip() == "ACP_BACKEND_OK"
            results["non_stream"] = "pass"
            results["usage_available"] = response.usage is not None
            stream = await client.chat.completions.create(
                model="antigravity",
                stream=True,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply exactly ACP_STREAM_OK. Do not use tools.",
                    }
                ],
            )
            chunks = [chunk async for chunk in stream]
            assert (
                "".join(c.choices[0].delta.content or "" for c in chunks).strip()
                == "ACP_STREAM_OK"
            )
            assert chunks[-1].choices[0].finish_reason == "stop"
            results["stream"] = "pass"
            stream = await client.chat.completions.create(
                model="antigravity",
                stream=True,
                messages=[
                    {
                        "role": "user",
                        "content": "Write the numbers from 1 to 5000, one per line. Use plain text only and no tools.",
                    }
                ],
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    break
            await stream.close()

            async def active_count():
                return len(runtime.jobs)

            async with asyncio.timeout(20):
                while await asyncio.wrap_future(runtime.submit(active_count())):
                    await asyncio.sleep(0.05)
            results["disconnect_cleanup"] = "pass"
            try:
                await client.chat.completions.create(
                    model="not-configured", messages=[{"role": "user", "content": "x"}]
                )
                raise AssertionError("Unknown alias was accepted")
            except BadRequestError:
                results["unknown_alias"] = "pass"
        print(json.dumps(results, indent=2))
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 20)
        finally:
            await asyncio.to_thread(runtime.close)
            sock.close()


if __name__ == "__main__":
    asyncio.run(main())
