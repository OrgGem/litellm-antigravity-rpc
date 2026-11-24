import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_MODE"] = "PRODUCTION"

from test_backend import backend, is_alive  # noqa: E402

from litellm_antigravity_rpc.contracts import Request  # noqa: E402
from litellm_antigravity_rpc.errors import GatewayError  # noqa: E402
from litellm_antigravity_rpc.provider import AntigravityLLM  # noqa: E402
from litellm_antigravity_rpc.runtime import Runtime  # noqa: E402


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_during_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            runtime = Runtime(backend("hold_initialize", audit))
            startup = runtime.submit(runtime.backend.startup())
            try:
                async with asyncio.timeout(5):
                    while not audit.exists():
                        await asyncio.sleep(0.01)
                await asyncio.to_thread(runtime.close)
                self.assertTrue(startup.done())
                self.assertTrue(startup.cancelled())
                self.assertFalse(runtime.thread.is_alive())
                self.assertFalse(
                    is_alive(json.loads(audit.read_text().splitlines()[0])["pid"])
                )
            finally:
                await asyncio.to_thread(runtime.close)

    async def wait_prompt(self, audit):
        async with asyncio.timeout(5):
            while not audit.exists() or "session/prompt" not in audit.read_text():
                await asyncio.sleep(0.01)

    async def test_shutdown_with_active_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            runtime = Runtime(backend("hold", audit))
            provider = AntigravityLLM(runtime)

            async def consume():
                return [e async for e in provider.aevents(Request("x", "r", "test"))]

            task = asyncio.create_task(consume())
            try:
                await self.wait_prompt(audit)
                await asyncio.to_thread(runtime.close)
                with self.assertRaises(GatewayError) as caught:
                    await asyncio.wait_for(task, 5)
                self.assertIn(
                    caught.exception.code, {"request_cancelled", "shutting_down"}
                )
                self.assertFalse(runtime.thread.is_alive())
                self.assertFalse(runtime.jobs)
                self.assertIn("session/cancel", audit.read_text())
                self.assertFalse(
                    is_alive(json.loads(audit.read_text().splitlines()[0])["pid"])
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.to_thread(runtime.close)

    async def test_runtime_backpressure_cleans_process_before_error(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            runtime = Runtime(backend("paced", audit, queue_size=2))
            try:
                job = await asyncio.wrap_future(
                    runtime.submit(runtime.start(Request("x", "r", "test")))
                )
                await asyncio.wait_for(
                    asyncio.wrap_future(runtime.submit(job.finished.wait())), 5
                )
                with self.assertRaises(GatewayError) as caught:
                    await asyncio.wrap_future(runtime.submit(runtime.next(job)))
                self.assertEqual(caught.exception.code, "slow_consumer")
                self.assertIn("session/cancel", audit.read_text())
                self.assertFalse(
                    is_alive(json.loads(audit.read_text().splitlines()[0])["pid"])
                )
                await asyncio.wrap_future(runtime.release(job))
            finally:
                await asyncio.to_thread(runtime.close)

    async def test_admission_limit_and_cancel_before_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"
            runtime = Runtime(backend("hold", audit, concurrency=1, max_pending=2))
            try:
                first = await asyncio.wrap_future(
                    runtime.submit(runtime.start(Request("x", "1", "test")))
                )
                await self.wait_prompt(audit)
                second = await asyncio.wrap_future(
                    runtime.submit(runtime.start(Request("x", "2", "test")))
                )
                with self.assertRaises(GatewayError) as caught:
                    await asyncio.wrap_future(
                        runtime.submit(runtime.start(Request("x", "3", "test")))
                    )
                self.assertEqual(caught.exception.status, 429)
                await asyncio.wrap_future(runtime.release(second))
                self.assertEqual(audit.read_text().count("initialize"), 1)
                await asyncio.wrap_future(runtime.release(first))
                self.assertFalse(runtime.jobs)
            finally:
                await asyncio.to_thread(runtime.close)
