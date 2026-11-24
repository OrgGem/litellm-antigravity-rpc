"""Small ASGI host for the CustomLLM adapter, with explicit disconnect ownership."""

import asyncio
import contextlib
import hmac
import json
import time
import uuid

from .contracts import Request
from .errors import GatewayError
from .provider import AntigravityLLM, usage_dict, validate_messages
from .responses_guard import (
    input_to_messages,
    responses_usage_dict,
    validate_responses_payload,
)
from .transport import strict_json


def _responses_completion_state(finish_reason):
    if finish_reason == "length":
        return "incomplete", {"reason": "max_output_tokens"}, "response.incomplete"
    if finish_reason == "content_filter":
        return "incomplete", {"reason": "content_filter"}, "response.incomplete"
    return "completed", None, "response.completed"


def _response_object(
    response_id,
    created_at,
    model,
    status,
    output,
    *,
    instructions=None,
    parallel_tool_calls=True,
    tool_choice="auto",
    usage=None,
    error=None,
    incomplete_details=None,
):
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "instructions": instructions,
        "model": model,
        "output": output,
        "parallel_tool_calls": parallel_tool_calls,
        "tool_choice": tool_choice,
        "tools": [],
        "usage": usage,
    }


def _output_message(item_id, text, status="completed"):
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class App:
    def __init__(self, runtime):
        self.runtime = runtime
        self.config = runtime.backend.config
        if len(self.config.api_key) < 16 or not self.config.api_key.isascii():
            raise ValueError("API key must have at least 16 ASCII characters")
        self.provider = AntigravityLLM(runtime)
        self.ready = False

    async def _json(self, send, status, data, request_id):
        body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-request-id", request_id.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _read_json(self, receive):
        data = bytearray()
        async with asyncio.timeout(10):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return None
                if message["type"] != "http.request":
                    raise GatewayError("invalid_request", 400)
                data.extend(message.get("body", b""))
                if len(data) > self.config.max_input_bytes:
                    raise GatewayError("request_too_large", 413)
                if not message.get("more_body", False):
                    break
        try:
            return strict_json(data.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise GatewayError("invalid_json", 400) from None

    async def _run_with_disconnect(self, receive, coro):
        async def disconnect():
            while (await receive())["type"] != "http.disconnect":
                pass

        response_task = asyncio.create_task(coro)
        disconnect_task = asyncio.create_task(disconnect())
        try:
            done, _ = await asyncio.wait(
                {response_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if response_task in done:
                await response_task
        finally:
            disconnect_task.cancel()
            response_task.cancel()
            await asyncio.gather(response_task, disconnect_task, return_exceptions=True)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._lifespan(receive, send)
        if scope["type"] != "http":
            return
        request_id = uuid.uuid4().hex
        started = False
        responses_sse = None

        async def bounded_send(message):
            nonlocal started
            async with asyncio.timeout(10):
                await send(message)
            if message["type"] == "http.response.start":
                started = True

        async def handle():
            nonlocal responses_sse
            auth = [
                v for k, v in scope.get("headers", []) if k.lower() == b"authorization"
            ]
            expected = ("Bearer " + self.config.api_key).encode("ascii")
            if len(auth) != 1 or not hmac.compare_digest(auth[0], expected):
                raise GatewayError("invalid_api_key", 401)
            path, method = scope["path"], scope["method"]
            if not self.ready:
                raise GatewayError("not_ready", 503)
            if path == "/health/ready" and method == "GET":
                return await self._json(
                    bounded_send,
                    200,
                    {"status": "ready", "model_access_verified": False},
                    request_id,
                )
            if path == "/v1/models" and method == "GET":
                return await self._json(
                    bounded_send,
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": alias,
                                "object": "model",
                                "created": 0,
                                "owned_by": "antigravity",
                            }
                            for alias in self.config.models
                        ],
                    },
                    request_id,
                )
            if method != "POST" or path not in (
                "/v1/chat/completions",
                "/v1/responses",
            ):
                raise GatewayError("not_found", 404)

            payload = await self._read_json(receive)
            if payload is None:
                return

            if path == "/v1/responses":
                validate_responses_payload(payload)
                alias = self.provider._resolve_alias(
                    payload["model"], self.config.models
                )
                messages, phases = input_to_messages(payload["input"])
                instructions = payload.get("instructions") or None
                text = validate_messages(
                    messages,
                    self.config.max_input_bytes,
                    self.config.flatten_for(alias),
                    phases=phases,
                )
                request = Request(text, request_id, alias, instructions=instructions)
                stream = bool(payload.get("stream", False))
                parallel = payload.get("parallel_tool_calls")
                if parallel is None:
                    parallel = True
                tool_choice = payload.get("tool_choice") or "auto"
                if stream:
                    responses_sse = {
                        "id": "resp_" + request_id,
                        "created_at": int(time.time()),
                        "model": alias,
                        "instructions": instructions,
                        "parallel_tool_calls": parallel,
                        "tool_choice": tool_choice,
                        "seq": 0,
                    }

                async def respond_responses():
                    async with contextlib.aclosing(
                        self.provider.aevents(request)
                    ) as events:
                        if stream:
                            await self._stream_responses(
                                events, responses_sse, bounded_send
                            )
                        else:
                            chunks, terminal = [], None
                            async for event in events:
                                if event.done:
                                    terminal = event
                                else:
                                    chunks.append(event.text)
                            if terminal is None:
                                raise GatewayError("missing_completion")
                            resp_status, incomplete_details, _ = (
                                _responses_completion_state(terminal.finish_reason)
                            )
                            body = _response_object(
                                "resp_" + request_id,
                                int(time.time()),
                                alias,
                                resp_status,
                                [
                                    _output_message(
                                        "msg_" + request_id,
                                        "".join(chunks),
                                        status=resp_status,
                                    )
                                ],
                                instructions=instructions,
                                parallel_tool_calls=parallel,
                                tool_choice=tool_choice,
                                usage=responses_usage_dict(terminal.usage),
                                incomplete_details=incomplete_details,
                            )
                            await self._json(bounded_send, 200, body, request_id)

                return await self._run_with_disconnect(receive, respond_responses())

            if not isinstance(payload, dict) or set(payload) - {
                "model",
                "messages",
                "stream",
                "stream_options",
            }:
                raise GatewayError("unsupported_parameters", 400)
            if (
                not isinstance(payload.get("model"), str)
                or type(payload.get("stream", False)) is not bool
            ):
                raise GatewayError("invalid_request", 400)
            stream = payload.get("stream", False)
            include_usage = False
            if "stream_options" in payload:
                options = payload["stream_options"]
                if (
                    not stream
                    or not isinstance(options, dict)
                    or set(options) != {"include_usage"}
                    or type(options["include_usage"]) is not bool
                ):
                    raise GatewayError("unsupported_parameters", 400)
                include_usage = options["include_usage"]
            request = self.provider._request(
                payload["model"], payload.get("messages"), request_id=request_id
            )

            async def respond_chat():
                async with contextlib.aclosing(
                    self.provider.aevents(request)
                ) as events:
                    if stream:
                        await self._stream(events, request, include_usage, bounded_send)
                    else:
                        text, terminal = [], None
                        async for event in events:
                            if event.done:
                                terminal = event
                            else:
                                text.append(event.text)
                        response = self.provider.response(
                            request.model, "".join(text), terminal
                        )
                        await self._json(
                            bounded_send,
                            200,
                            response.model_dump(exclude_none=True),
                            request_id,
                        )

            await self._run_with_disconnect(receive, respond_chat())

        try:
            await handle()
        except GatewayError as exc:
            if not started:
                await self._json(bounded_send, exc.status, exc.payload(), request_id)
            elif responses_sse is not None:
                await self._emit_responses_failed(responses_sse, exc.code, bounded_send)
            else:
                await bounded_send(
                    {
                        "type": "http.response.body",
                        "body": (
                            "data: " + json.dumps(exc.payload()) + "\n\n"
                        ).encode(),
                        "more_body": False,
                    }
                )
        except (TimeoutError, OSError):
            # A stalled/disconnected socket must still unwind and cancel ACP.
            if not started:
                with contextlib.suppress(TimeoutError, OSError):
                    error = GatewayError("deadline_exceeded", 504)
                    await self._json(
                        bounded_send, error.status, error.payload(), request_id
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            error = GatewayError("internal_error", 500)
            if not started:
                await self._json(bounded_send, 500, error.payload(), request_id)
            elif responses_sse is not None:
                await self._emit_responses_failed(
                    responses_sse, error.code, bounded_send
                )
            else:
                await bounded_send(
                    {
                        "type": "http.response.body",
                        "body": (
                            "data: " + json.dumps(error.payload()) + "\n\n"
                        ).encode(),
                        "more_body": False,
                    }
                )

    async def _emit_responses_failed(self, meta, code, send):
        seq = meta["seq"]
        meta["seq"] += 1
        failed = _response_object(
            meta["id"],
            meta["created_at"],
            meta["model"],
            "failed",
            [],
            instructions=meta["instructions"],
            parallel_tool_calls=meta["parallel_tool_calls"],
            tool_choice=meta["tool_choice"],
            error={"code": "server_error", "message": code},
        )
        payload = {
            "type": "response.failed",
            "sequence_number": seq,
            "response": failed,
        }
        await send(
            {
                "type": "http.response.body",
                "body": (
                    "event: response.failed\ndata: "
                    + json.dumps(payload, ensure_ascii=True)
                    + "\n\n"
                ).encode(),
                "more_body": False,
            }
        )

    async def _stream_responses(self, events, meta, send):
        first = await anext(events)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"x-accel-buffering", b"no"),
                    (b"x-request-id", meta["id"].removeprefix("resp_").encode()),
                ],
            }
        )
        item_id = "msg_" + meta["id"].removeprefix("resp_")

        async def send_event(event_type, body, more_body=True):
            seq = meta["seq"]
            meta["seq"] += 1
            payload = {"type": event_type, "sequence_number": seq, **body}
            await send(
                {
                    "type": "http.response.body",
                    "body": (
                        f"event: {event_type}\ndata: "
                        + json.dumps(payload, ensure_ascii=True)
                        + "\n\n"
                    ).encode(),
                    "more_body": more_body,
                }
            )

        in_progress = _response_object(
            meta["id"],
            meta["created_at"],
            meta["model"],
            "in_progress",
            [],
            instructions=meta["instructions"],
            parallel_tool_calls=meta["parallel_tool_calls"],
            tool_choice=meta["tool_choice"],
        )
        await send_event("response.created", {"response": in_progress})
        await send_event("response.in_progress", {"response": in_progress})
        await send_event(
            "response.output_item.added",
            {
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            },
        )
        await send_event(
            "response.content_part.added",
            {
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )

        chunks = []
        terminal = None
        event = first
        while event is not None:
            if event.done:
                terminal = event
            elif event.text:
                chunks.append(event.text)
                await send_event(
                    "response.output_text.delta",
                    {
                        "item_id": item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": event.text,
                        "logprobs": [],
                    },
                )
            event = await anext(events, None)
        if terminal is None:
            raise GatewayError("missing_completion")

        full_text = "".join(chunks)
        await send_event(
            "response.output_text.done",
            {
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "text": full_text,
                "logprobs": [],
            },
        )
        await send_event(
            "response.content_part.done",
            {
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": full_text, "annotations": []},
            },
        )
        resp_status, incomplete_details, terminal_event = _responses_completion_state(
            terminal.finish_reason
        )
        completed_item = _output_message(item_id, full_text, status=resp_status)
        await send_event(
            "response.output_item.done",
            {"output_index": 0, "item": completed_item},
        )
        completed_response = _response_object(
            meta["id"],
            meta["created_at"],
            meta["model"],
            resp_status,
            [completed_item],
            instructions=meta["instructions"],
            parallel_tool_calls=meta["parallel_tool_calls"],
            tool_choice=meta["tool_choice"],
            usage=responses_usage_dict(terminal.usage),
            incomplete_details=incomplete_details,
        )
        await send_event(
            terminal_event,
            {"response": completed_response},
            more_body=False,
        )

    async def _stream(self, events, request, include_usage, send):
        # First backend error can retain its HTTP status before headers commit.
        first = await anext(events)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"x-accel-buffering", b"no"),
                    (b"x-request-id", request.request_id.encode()),
                ],
            }
        )
        base = {
            "id": "chatcmpl-" + request.request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model,
        }

        async def emit(choices, usage=None):
            chunk = {**base, "choices": choices}
            if include_usage:
                chunk["usage"] = usage
            await send(
                {
                    "type": "http.response.body",
                    "body": (
                        "data: " + json.dumps(chunk, ensure_ascii=True) + "\n\n"
                    ).encode(),
                    "more_body": True,
                }
            )

        await emit(
            [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
        )
        terminal = None
        event = first
        while event is not None:
            if event.done:
                terminal = event
            else:
                await emit(
                    [
                        {
                            "index": 0,
                            "delta": {"content": event.text},
                            "finish_reason": None,
                        }
                    ]
                )
            event = await anext(events, None)
        if terminal is None:
            raise GatewayError("missing_completion")
        await emit([{"index": 0, "delta": {}, "finish_reason": terminal.finish_reason}])
        if include_usage:
            await emit([], usage_dict(terminal.usage))
        await send(
            {
                "type": "http.response.body",
                "body": b"data: [DONE]\n\n",
                "more_body": False,
            }
        )

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await asyncio.wrap_future(
                        self.runtime.submit(self.runtime.backend.startup())
                    )
                    self.ready = True
                    await send({"type": "lifespan.startup.complete"})
                except Exception:
                    await asyncio.to_thread(self.runtime.close)
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "ACP readiness validation failed",
                        }
                    )
                    return
            elif message["type"] == "lifespan.shutdown":
                self.ready = False
                await asyncio.to_thread(self.runtime.close)
                await send({"type": "lifespan.shutdown.complete"})
                return
