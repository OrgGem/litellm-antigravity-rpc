import asyncio
import contextlib
import logging
import time

from . import __version__
from .config import is_supported_cli_version
from .contracts import Event, Usage
from .errors import GatewayError
from .profile import Profile
from .transport import ACPClient

logger = logging.getLogger(__name__)


def select_option(options, category, value):
    if not isinstance(options, list):
        raise GatewayError("invalid_model_configuration")
    matches = [
        o for o in options if isinstance(o, dict) and o.get("category") == category
    ]
    if len(matches) != 1:
        raise GatewayError("unsupported_model_configuration", 400)
    option = matches[0]
    if option.get("type") != "select" or not isinstance(option.get("id"), str):
        raise GatewayError("unsupported_model_configuration", 400)
    values = []
    entries = option.get("options", [])
    if not isinstance(entries, list):
        raise GatewayError()
    for entry in entries:
        if not isinstance(entry, dict):
            raise GatewayError()
        nested = entry.get("options", [entry])
        if not isinstance(nested, list) or any(not isinstance(v, dict) for v in nested):
            raise GatewayError()
        values.extend(v.get("value") for v in nested)
    if value not in values:
        raise GatewayError("model_not_available", 400)
    return option["id"]


def parse_usage(raw):
    if raw is None:
        return Usage()
    if not isinstance(raw, dict):
        raise GatewayError("invalid_usage")

    def counter(key):
        if key not in raw or raw[key] is None:
            return None
        value = raw[key]
        if type(value) is not int or value < 0:
            raise GatewayError("invalid_usage")
        return value

    input_tokens = counter("inputTokens")
    output_tokens = counter("outputTokens")
    total_tokens = counter("totalTokens")
    cached_tokens = counter("cachedReadTokens")
    if cached_tokens is None:
        cached_tokens = counter("cachedInputTokens")
    cache_write_tokens = counter("cachedWriteTokens")
    if cache_write_tokens is None:
        cache_write_tokens = counter("cacheWriteInputTokens")
    reasoning_tokens = counter("thoughtTokens")
    if reasoning_tokens is None:
        reasoning_tokens = counter("reasoningOutputTokens")

    if input_tokens is not None:
        for detail in (cached_tokens, cache_write_tokens):
            if detail is not None and detail > input_tokens:
                raise GatewayError("invalid_usage")
    elif cached_tokens is not None or cache_write_tokens is not None:
        raise GatewayError("invalid_usage")

    if output_tokens is not None:
        if reasoning_tokens is not None and reasoning_tokens > output_tokens:
            raise GatewayError("invalid_usage")
    elif reasoning_tokens is not None:
        raise GatewayError("invalid_usage")

    if input_tokens is not None and output_tokens is not None and total_tokens is None:
        total_tokens = input_tokens + output_tokens

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def format_prompt_text(request):
    if request.instructions:
        return (
            "Developer instructions (descriptive context):\n"
            + request.instructions
            + "\n\n"
            + request.text
        )
    return request.text


class ACPBackend:
    def __init__(self, config, command_factory=None):
        self.config = config
        # Injection is for subprocess fixtures, never exposed in HTTP input.
        self.command_factory = command_factory
        self.semaphore = asyncio.Semaphore(config.concurrency)
        self.pending = 0
        self.executable = None
        self.ready = False
        self.closed = False
        self.active = set()
        self.startup_task = None

    async def startup(self):
        self.startup_task = asyncio.current_task()
        try:
            await self._startup()
        finally:
            self.startup_task = None

    async def _startup(self):
        if self.command_factory is None:
            self.executable = self.config.resolve_executable()
            client = ACPClient(self.config, lambda message: None)
            profile = Profile()
            try:
                async with asyncio.timeout(10):
                    await client.start(
                        [self.executable, "--version"],
                        profile.workspace,
                        profile.env,
                        read=False,
                    )
                    version = await client.proc.stdout.read(256)
                    await client.proc.wait()
                    if not is_supported_cli_version(version.decode(errors="replace")):
                        raise GatewayError("unsupported_cli_version", 503)
            finally:
                await client.close()
                profile.close()
        for alias in self.config.models:
            async for _ in self.stream(None, alias=alias):
                pass
        self.ready = True

    async def _configure(self, client, sid, options, model):
        for category, value in (
            ("model", model.model),
            ("thought_level", model.thought_level),
        ):
            if value is None:
                continue
            option_id = select_option(options, category, value)
            result = await client.request(
                "session/set_config_option",
                {
                    "sessionId": sid,
                    "configId": option_id,
                    "value": value,
                },
            )
            options = result.get("configOptions")
            select_option(options, category, value)
            if not any(
                o.get("id") == option_id and o.get("currentValue") == value
                for o in options
            ):
                raise GatewayError("model_selection_failed")

    async def stream(self, request, alias=None):
        alias = alias if request is None else request.model
        if self.closed:
            raise GatewayError("shutting_down", 503)
        if alias not in self.config.models:
            raise GatewayError("unknown_model", 400)
        if self.pending >= self.config.max_pending:
            raise GatewayError("queue_full", 429)
        self.pending += 1
        owner = asyncio.current_task()
        self.active.add(owner)
        start = time.monotonic()
        status = "error"
        first = None
        try:
            async with asyncio.timeout(self.config.timeout):
                async with self.semaphore:
                    async with contextlib.aclosing(
                        self._session(request, self.config.models[alias])
                    ) as events:
                        async for event in events:
                            if event.text and first is None:
                                first = time.monotonic() - start
                            yield event
                    status = "ok"
        except TimeoutError:
            status = "timeout"
            raise GatewayError("deadline_exceeded", 504) from None
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except (OSError, ValueError):
            raise GatewayError("upstream_unavailable", 502) from None
        finally:
            self.pending -= 1
            self.active.discard(owner)
            logger.info(
                "request=%s model=%s status=%s duration=%.3f first_text=%s",
                request.request_id if request else "readiness",
                alias,
                status,
                time.monotonic() - start,
                round(first, 3) if first is not None else None,
            )

    async def _session(self, request, model):
        profile = Profile()
        updates = asyncio.Queue(self.config.queue_size)
        sid = None
        prompt = None
        total_bytes = 0

        def on_update(message):
            nonlocal total_bytes
            if message["method"] != "session/update":
                raise GatewayError("unexpected_notification")
            params = message.get("params")
            if not isinstance(params, dict) or (
                sid is not None and params.get("sessionId") != sid
            ):
                raise GatewayError()
            update = params.get("update")
            if not isinstance(update, dict):
                raise GatewayError()
            kind = update.get("sessionUpdate")
            if kind in {"tool_call", "tool_call_update"}:
                raise GatewayError("unexpected_tool_activity")
            if kind == "agent_message_chunk":
                content = update.get("content")
                if (
                    prompt is None
                    or not isinstance(content, dict)
                    or content.get("type") != "text"
                    or not isinstance(content.get("text"), str)
                ):
                    raise GatewayError("unsupported_content")
                text = content["text"]
                total_bytes += len(text.encode("utf-8"))
                if total_bytes > self.config.max_output_bytes:
                    raise GatewayError("response_too_large")
                if text:
                    try:
                        updates.put_nowait(Event(text=text))
                    except asyncio.QueueFull:
                        raise GatewayError("slow_consumer") from None
            elif kind not in {
                "user_message_chunk",
                "agent_thought_chunk",
                "plan",
                "available_commands_update",
                "config_option_update",
                "current_mode_update",
                "session_info_update",
                "usage_update",
            }:
                raise GatewayError("unsupported_update")

        client = ACPClient(self.config, on_update)
        try:
            command = (
                self.command_factory(profile)
                if self.command_factory
                else profile.command(
                    self.executable or self.config.resolve_executable()
                )
            )
            await client.start(command, profile.workspace, profile.env)
            initialize = await client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {
                        "name": "litellm-antigravity-rpc",
                        "version": __version__,
                    },
                },
            )
            if (
                type(initialize.get("protocolVersion")) is not int
                or initialize["protocolVersion"] != 1
                or not isinstance(initialize.get("agentCapabilities"), dict)
            ):
                raise GatewayError("protocol_version_mismatch")
            if self.command_factory is None and (
                not isinstance(initialize.get("agentInfo"), dict)
                or not is_supported_cli_version(initialize["agentInfo"].get("version"))
            ):
                raise GatewayError("unsupported_cli_version", 503)
            session = await client.request(
                "session/new", {"cwd": str(profile.workspace), "mcpServers": []}
            )
            sid = session.get("sessionId")
            if not isinstance(sid, str) or not sid:
                raise GatewayError()
            await self._configure(client, sid, session.get("configOptions"), model)
            if request is None:
                return
            prompt = asyncio.create_task(
                client.request(
                    "session/prompt",
                    {
                        "sessionId": sid,
                        "prompt": [
                            {"type": "text", "text": format_prompt_text(request)}
                        ],
                    },
                )
            )
            while True:
                if client.failure.done():
                    raise client.failure.result()
                if not updates.empty():
                    yield updates.get_nowait()
                    continue
                if prompt.done():
                    result = prompt.result()
                    break
                get = asyncio.create_task(updates.get())
                try:
                    done, _ = await asyncio.wait(
                        {get, prompt, client.failure},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if client.failure.done():
                        raise client.failure.result()
                    if get in done:
                        yield get.result()
                finally:
                    get.cancel()
                    await asyncio.gather(get, return_exceptions=True)
            reason = result.get("stopReason")
            mapping = {
                "end_turn": "stop",
                "max_tokens": "length",
                "max_turn_requests": "length",
                "refusal": "content_filter",
            }
            if reason not in mapping:
                raise GatewayError(
                    "upstream_cancelled"
                    if reason == "cancelled"
                    else "unsupported_stop_reason"
                )
            yield Event(
                done=True,
                finish_reason=mapping[reason],
                usage=parse_usage(result.get("usage")),
            )
        finally:
            # Cleanup gets its own bounded budget after the request deadline.
            async def cleanup():
                if sid and prompt and not prompt.done():
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(self.config.cleanup_timeout):
                            await client.notify("session/cancel", {"sessionId": sid})
                            await asyncio.shield(prompt)
                if prompt:
                    prompt.cancel()
                    await asyncio.gather(prompt, return_exceptions=True)
                if sid and not client.failure.done():
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(self.config.cleanup_timeout):
                            await client.request("session/close", {"sessionId": sid})
                try:
                    await client.close()
                finally:
                    profile.close()

            task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def close(self):
        self.closed = True
        self.ready = False
        tasks = self.active | ({self.startup_task} if self.startup_task else set())
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
