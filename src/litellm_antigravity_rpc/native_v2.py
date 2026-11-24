"""Fail-closed ACP native seam for frozen agent-rpc 0.1.

Client-executed tool bridging, reasoning export and multi-key profile isolation
are not verified.  The private fixture seam exercises ACP semantics without
starting an authenticated CLI or running a model.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .backend import select_option
from .config import Config
from .profile import Profile
from .transport import ACPClient

CONTRACT_VERSION = "0.1"
CONTRACT_HASH = "749182d456eb363be2d63b2a98d66d0f1bf203b93d8a16ed4a8bdd90834e5fc1"
SUPPORTED_CLI_VERSION = "1.5.3"
_END = object()


class DriverError(RuntimeError):
    """Sanitized error, without native output, prompts or local paths."""

    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def _opaque():
    return uuid.uuid4().hex


def _text(value, limit=131072):
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


def _feature(reason):
    return {
        "state": "blocked",
        "evidence_level": "source",
        "live_verified": False,
        "evidence": ["AG-01a-historical-text-only"],
        "reason": reason,
    }


@dataclass(eq=False)
class _Session:
    owner: Any = field(repr=False)
    context: dict = field(repr=False)
    config: dict = field(repr=False)
    loop: asyncio.AbstractEventLoop
    client: Any = field(default=None, repr=False)
    profile: Any = field(default=None, repr=False)
    native_session: str | None = field(default=None, repr=False)
    cleanup_task: asyncio.Task | None = field(default=None, repr=False)
    active: Any = field(default=None, repr=False)
    started: bool = False
    closed: bool = False


@dataclass(eq=False)
class _Turn:
    session: _Session
    input: dict = field(repr=False)
    invocation_id: str = field(default_factory=_opaque)
    sequence: int = 0
    usage_sequence: int = 0
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(64), repr=False)
    failure: asyncio.Future | None = field(default=None, repr=False)
    prompt_task: asyncio.Task | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    text_bytes: int = 0
    prompt_started: bool = False
    cancelled: bool = False
    finished: bool = False
    producer_ended: bool = False
    consumer_active: bool = False
    run_started: bool = False


class AntigravityNativeDriver:
    def __init__(self):
        self._fixture_client_factory = None

    @classmethod
    def _for_protocol_tests(cls, client_factory):
        """Private fake-client seam; not a readiness or authorization bypass."""
        driver = cls()
        driver._fixture_client_factory = client_factory
        return driver

    async def capabilities(self, profile: dict, model: str | None) -> dict:
        if model is not None and not _text(model, 200):
            raise DriverError("invalid_model", 400)
        profile_id = (
            profile.get("profile_id", "unconfigured")
            if isinstance(profile, dict)
            else "unconfigured"
        )
        if not _text(profile_id, 256) or not profile_id:
            profile_id = "unconfigured"
        return {
            "contract_version": CONTRACT_VERSION,
            "contract_hash": CONTRACT_HASH,
            "backend": "antigravity",
            "cli_version": None,
            "sdk_version": None,
            "model": model,
            "profile_id": profile_id,
            "transport": "stdio-acp",
            "features": {
                name: _feature("live_profile_or_capability_unverified")
                for name in (
                    "text",
                    "client_tools",
                    "parallel_tools",
                    "reasoning_output",
                    "reasoning_control",
                    "usage",
                    "output_token_limit",
                    "profile_isolation",
                )
            },
            "tool_choices": [],
            "reasoning_efforts": [],
            "reasoning_kinds": [],
            "limitations": [
                "v2_not_live_verified",
                "client_tool_bridge_unavailable",
                "single_request_session",
            ],
        }

    @staticmethod
    def _production_profile_verified(_context, _config):
        # No config flag can turn source/fixture evidence into a live proof.
        return False

    async def open_session(self, context, config):
        if not isinstance(context, dict) or not isinstance(config, dict):
            raise DriverError("invalid_context", 400)
        limits = context.get("limits")
        if (
            not _text(context.get("profile_id"), 256)
            or not context["profile_id"]
            or not _text(context.get("profile_revision"), 256)
            or not context["profile_revision"]
            or config.get("profile_id") != context["profile_id"]
            or config.get("profile_revision") != context["profile_revision"]
            or not isinstance(context.get("locator"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", context["locator"])
            or not isinstance(limits, dict)
            or type(limits.get("cleanup_seconds")) not in (int, float)
            or not 0 < limits["cleanup_seconds"] <= 60
        ):
            raise DriverError("invalid_context", 400)
        if (
            self._fixture_client_factory is None
            and not self._production_profile_verified(context, config)
        ):
            raise DriverError("profile_isolation_unverified", 503)
        return _Session(
            self, copy.deepcopy(context), dict(config), asyncio.get_running_loop()
        )

    def _check_session(self, session):
        if (
            not isinstance(session, _Session)
            or session.owner is not self
            or session.loop is not asyncio.get_running_loop()
        ):
            raise DriverError("invalid_session", 409)
        if session.closed:
            raise DriverError("session_closed", 410)

    @staticmethod
    def _validate_input(session, value):
        required = {
            "messages",
            "history_digest",
            "toolset_digest",
            "model_alias",
            "resolved_model",
            "tools",
            "round_policy",
            "reasoning_output",
            "reasoning_effort",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise DriverError("invalid_request", 400)
        if session.active is not None:
            raise DriverError("session_busy", 409)
        if session.started:
            raise DriverError("session_reuse_unavailable", 409)
        if value["tools"] != []:
            raise DriverError("client_tools_unavailable", 400)
        policy = value["round_policy"]
        if (
            not isinstance(policy, dict)
            or set(policy)
            - {"tool_choice", "parallel_tool_calls", "max_completion_tokens"}
            or policy.get("tool_choice") not in ("auto", "none")
            or policy.get("parallel_tool_calls") is not False
            or policy.get("max_completion_tokens") is not None
            or value["reasoning_effort"] is not None
            or value["reasoning_output"] != "off"
        ):
            raise DriverError("unsupported_parameter", 400)
        if (
            not isinstance(value["model_alias"], str)
            or not value["model_alias"]
            or not _text(value["resolved_model"], 200)
            or not value["resolved_model"]
            or not isinstance(value["history_digest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["history_digest"])
            or not isinstance(value["toolset_digest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["toolset_digest"])
        ):
            raise DriverError("invalid_request", 400)
        messages = value["messages"]
        if (
            not isinstance(messages, list)
            or len(messages) != 1
            or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"}
            or messages[0]["role"] != "user"
            or not _text(messages[0]["content"], 128 * 1024)
        ):
            raise DriverError("history_bootstrap_unsupported", 400)

    async def begin_turn(self, session, turn_input):
        self._check_session(session)
        self._validate_input(session, turn_input)
        deadlines = [
            session.context["limits"].get("native_deadline"),
            session.context["limits"].get("chain_deadline"),
        ]
        inference_seconds = session.context["limits"].get("inference_seconds")
        if (
            type(inference_seconds) not in (int, float)
            or not math.isfinite(inference_seconds)
            or inference_seconds <= 0
        ):
            raise DriverError("invalid_context", 400)
        if any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value <= time.monotonic()
            for value in deadlines
        ):
            raise DriverError("deadline_exceeded", 504)
        turn = _Turn(session, copy.deepcopy(turn_input))
        turn.failure = asyncio.get_running_loop().create_future()
        session.active, session.started = turn, True
        # No tool suspension exists in this path: the inference budget applies
        # continuously until the native prompt reaches a terminal response.
        inference_deadline = time.monotonic() + inference_seconds
        turn.task = asyncio.create_task(
            self._run(turn, min(*deadlines, inference_deadline))
        )
        turn.task.add_done_callback(
            lambda task: self._finalize_before_start(turn, task)
        )
        return turn

    @staticmethod
    def _finalize_before_start(turn, task):
        if task.cancelled() and not turn.run_started and not turn.producer_ended:
            turn.cancelled = True
            turn.finished = True
            turn.session.closed = True
            if turn.session.active is turn:
                turn.session.active = None
            turn.queue.put_nowait(_END)
            turn.producer_ended = True

    @staticmethod
    def _emit(turn, kind, payload):
        event = {
            "type": kind,
            "invocation_id": turn.invocation_id,
            "sequence": turn.sequence,
            "payload": payload,
        }
        turn.sequence += 1
        try:
            turn.queue.put_nowait(event)
        except asyncio.QueueFull:
            raise DriverError("backend_backpressure", 502) from None

    @staticmethod
    def _unknown_usage(turn):
        turn.usage_sequence += 1
        return {
            "invocation_id": turn.invocation_id,
            "step_id": None,
            "epoch": turn.invocation_id,
            "event_id": _opaque(),
            "sequence": turn.usage_sequence,
            "scope": "native_turn",
            "semantics": "snapshot",
            "coverage": None,
            "source": "antigravity_acp_scope_unverified",
            "unit": "tokens",
            "completeness": "unknown",
            "final": True,
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "reasoning_tokens": None,
        }

    def _on_update(self, turn, message):
        if turn.cancelled or turn.finished or turn.failure.done():
            return
        try:
            if (
                not isinstance(message, dict)
                or message.get("method") != "session/update"
            ):
                raise DriverError("invalid_backend_response", 502)
            params = message.get("params")
            if not isinstance(params, dict):
                raise DriverError("invalid_backend_response", 502)
            update = params.get("update")
            if not isinstance(update, dict):
                raise DriverError("invalid_backend_response", 502)
            kind = update.get("sessionUpdate")
            if turn.session.native_session is None:
                if kind in {
                    "available_commands_update",
                    "config_option_update",
                    "current_mode_update",
                    "session_info_update",
                }:
                    return
                raise DriverError("invalid_backend_response", 502)
            if params.get("sessionId") != turn.session.native_session:
                raise DriverError("invalid_backend_response", 502)
            if kind in {"tool_call", "tool_call_update"}:
                raise DriverError("unexpected_tool_activity", 502)
            if kind == "agent_message_chunk":
                content = update.get("content")
                if (
                    not turn.prompt_started
                    or not isinstance(content, dict)
                    or content.get("type") != "text"
                    or not _text(content.get("text"))
                ):
                    raise DriverError("invalid_backend_response", 502)
                text = content["text"]
                turn.text_bytes += len(text.encode("utf-8"))
                if turn.text_bytes > 2 * 1024 * 1024:
                    raise DriverError("response_too_large", 502)
                if text:
                    self._emit(turn, "text_delta", {"text": text})
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
                raise DriverError("invalid_backend_response", 502)
        except DriverError as error:
            if not turn.failure.done():
                turn.failure.set_result(error)
        except Exception:
            if not turn.failure.done():
                turn.failure.set_result(DriverError("invalid_backend_response", 502))

    async def _create_client(self, turn):
        session = turn.session
        if self._fixture_client_factory is not None:
            session.client = await self._fixture_client_factory(
                session.config, lambda msg: self._on_update(turn, msg)
            )
            return
        if not self._production_profile_verified(session.context, session.config):
            raise DriverError("profile_isolation_unverified", 503)
        config = session.config.get("adapter_config")
        if not isinstance(config, Config):
            raise DriverError("invalid_transport_config", 400)
        session.profile = Profile()
        executable = config.resolve_executable()
        version_client = ACPClient(config, lambda _: None)
        try:
            async with asyncio.timeout(10):
                await version_client.start(
                    [executable, "--version"],
                    session.profile.workspace,
                    session.profile.env,
                    read=False,
                )
                output = await version_client.proc.stdout.read(256)
                await version_client.proc.wait()
                if (
                    version_client.proc.returncode != 0
                    or output.decode("utf-8").strip() != SUPPORTED_CLI_VERSION
                ):
                    raise DriverError("unsupported_cli_version", 503)
        finally:
            await version_client.close()
        session.client = ACPClient(config, lambda msg: self._on_update(turn, msg))
        await session.client.start(
            session.profile.command(executable),
            session.profile.workspace,
            session.profile.env,
        )

    async def _setup(self, turn):
        session = turn.session
        await self._create_client(turn)
        client = session.client
        result = await client.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {
                    "name": "litellm_antigravity_rpc_v2",
                    "version": "0.1.0.dev0",
                },
            },
        )
        info = result.get("agentInfo")
        if (
            type(result.get("protocolVersion")) is not int
            or result["protocolVersion"] != 1
            or not isinstance(result.get("agentCapabilities"), dict)
            or not isinstance(info, dict)
            or info.get("version") != SUPPORTED_CLI_VERSION
        ):
            raise DriverError("unsupported_cli_version", 503)
        cwd = (
            str(session.profile.workspace)
            if session.profile is not None
            else "/fixture-workspace"
        )
        created = await client.request("session/new", {"cwd": cwd, "mcpServers": []})
        identifier = created.get("sessionId")
        if not isinstance(identifier, str) or not identifier:
            raise DriverError("invalid_backend_response", 502)
        session.native_session = identifier
        try:
            option_id = select_option(
                created.get("configOptions"), "model", turn.input["resolved_model"]
            )
        except Exception:
            raise DriverError("model_selection_unavailable", 503) from None
        changed = await client.request(
            "session/set_config_option",
            {
                "sessionId": identifier,
                "configId": option_id,
                "value": turn.input["resolved_model"],
            },
        )
        if not any(
            isinstance(option, dict)
            and option.get("id") == option_id
            and option.get("currentValue") == turn.input["resolved_model"]
            for option in changed.get("configOptions", [])
        ):
            raise DriverError("model_selection_failed", 503)

    async def _run(self, turn, deadline):
        turn.run_started = True
        session = turn.session
        terminal = None
        cleanup_ok = False
        try:
            async with asyncio.timeout_at(deadline):
                await self._setup(turn)
                if turn.failure.done():
                    raise turn.failure.result()
                if session.client.failure.done():
                    raise DriverError("backend_unavailable", 502)
                turn.prompt_started = True
                turn.prompt_task = asyncio.create_task(
                    session.client.request(
                        "session/prompt",
                        {
                            "sessionId": session.native_session,
                            "prompt": [
                                {
                                    "type": "text",
                                    "text": turn.input["messages"][0]["content"],
                                }
                            ],
                        },
                    )
                )
                done, _ = await asyncio.wait(
                    {turn.prompt_task, turn.failure, session.client.failure},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if turn.failure in done:
                    raise turn.failure.result()
                if session.client.failure in done:
                    raise DriverError("backend_unavailable", 502)
                result = await turn.prompt_task
                if turn.failure.done():
                    raise turn.failure.result()
                if session.client.failure.done():
                    raise DriverError("backend_unavailable", 502)
                mapping = {
                    "end_turn": "stop",
                    "max_tokens": "length",
                    "max_turn_requests": "length",
                    "refusal": "content_filter",
                }
                reason = result.get("stopReason") if isinstance(result, dict) else None
                if reason not in mapping:
                    raise DriverError("backend_turn_failed", 502)
                usage = result.get("usage")
                if usage is not None:
                    if not isinstance(usage, dict) or any(
                        field in usage
                        and usage[field] is not None
                        and (type(usage[field]) is not int or usage[field] < 0)
                        for field in (
                            "inputTokens",
                            "outputTokens",
                            "cachedReadTokens",
                            "cachedWriteTokens",
                            "thoughtTokens",
                            "totalTokens",
                        )
                    ):
                        raise DriverError("invalid_backend_usage", 502)
                self._emit(turn, "usage", self._unknown_usage(turn))
                terminal = {
                    "kind": "native_terminal",
                    "finish_reason": mapping[reason],
                    "usage_completeness": "unknown",
                }
        except asyncio.CancelledError:
            turn.cancelled = True
            raise
        except TimeoutError:
            self._safe_error(turn, "deadline_exceeded", 504)
        except DriverError as error:
            self._safe_error(turn, error.code, error.status)
        except Exception:
            self._safe_error(turn, "backend_unavailable", 502)
        finally:
            turn.finished = True
            session.closed = True
            try:
                async with asyncio.timeout(
                    session.context["limits"]["cleanup_seconds"]
                ):
                    await self._release(session, turn)
                cleanup_ok = True
            except TimeoutError:
                self._safe_error(turn, "cleanup_timeout", 503)
            except Exception:
                self._safe_error(turn, "cleanup_failed", 503)
            finally:
                session.active = None
                try:
                    if cleanup_ok and terminal is not None and not turn.cancelled:
                        self._emit(turn, "native_turn_ended", terminal)
                    turn.queue.put_nowait(_END)
                except (asyncio.QueueFull, DriverError):
                    while not turn.queue.empty():
                        turn.queue.get_nowait()
                    self._safe_error(turn, "backend_backpressure", 502)
                    turn.queue.put_nowait(_END)
                turn.producer_ended = True

    @staticmethod
    def _safe_error(turn, code, status):
        try:
            AntigravityNativeDriver._emit(
                turn, "backend_error", {"code": code, "status": status}
            )
        except DriverError:
            pass

    @staticmethod
    async def _release(session, turn):
        if session.cleanup_task is None:

            async def close_owned():
                client_closed = False
                try:
                    client = session.client
                    if client is not None:
                        if turn.prompt_task and not turn.prompt_task.done():
                            if session.native_session:
                                with contextlib.suppress(Exception):
                                    async with asyncio.timeout(
                                        min(
                                            2,
                                            session.context["limits"]["cleanup_seconds"]
                                            / 2,
                                        )
                                    ):
                                        await client.notify(
                                            "session/cancel",
                                            {"sessionId": session.native_session},
                                        )
                            turn.prompt_task.cancel()
                            await asyncio.gather(
                                turn.prompt_task, return_exceptions=True
                            )
                        if session.native_session:
                            with contextlib.suppress(Exception):
                                async with asyncio.timeout(
                                    min(
                                        2,
                                        session.context["limits"]["cleanup_seconds"]
                                        / 2,
                                    )
                                ):
                                    await client.request(
                                        "session/close",
                                        {"sessionId": session.native_session},
                                    )
                        await client.close()
                    client_closed = True
                finally:
                    # Do not remove a workspace that a failed close may leave in use.
                    if client_closed and session.profile is not None:
                        session.profile.close()

            session.cleanup_task = asyncio.create_task(close_owned())
        await asyncio.shield(session.cleanup_task)
        session.client, session.profile = None, None

    async def events(self, turn):
        if (
            not isinstance(turn, _Turn)
            or turn.session.owner is not self
            or turn.session.loop is not asyncio.get_running_loop()
        ):
            raise DriverError("invalid_turn", 409)
        if turn.consumer_active:
            raise DriverError("event_consumer_exists", 409)
        turn.consumer_active = True
        try:
            while True:
                if turn.producer_ended and turn.queue.empty():
                    return
                event = await turn.queue.get()
                if event is _END:
                    return
                yield event
        finally:
            turn.consumer_active = False

    async def submit_tool_results(self, turn, results):
        if (
            not isinstance(turn, _Turn)
            or turn.session.owner is not self
            or turn.session.loop is not asyncio.get_running_loop()
        ):
            raise DriverError("invalid_turn", 409)
        raise DriverError("client_tools_unavailable", 400)

    async def cancel(self, turn, reason, cleanup_deadline):
        if (
            not isinstance(turn, _Turn)
            or turn.session.owner is not self
            or turn.session.loop is not asyncio.get_running_loop()
            or not isinstance(reason, str)
        ):
            raise DriverError("invalid_turn", 409)
        session = turn.session
        if session.active is not turn:
            return
        if turn.finished:
            if turn.task and not turn.task.done():
                try:
                    async with asyncio.timeout(
                        max(0, cleanup_deadline - time.monotonic())
                    ):
                        await asyncio.shield(
                            asyncio.gather(turn.task, return_exceptions=True)
                        )
                except TimeoutError:
                    raise DriverError("cleanup_timeout", 503) from None
            if session.cleanup_task:
                try:
                    async with asyncio.timeout(
                        max(0, cleanup_deadline - time.monotonic())
                    ):
                        await asyncio.shield(session.cleanup_task)
                except TimeoutError:
                    raise DriverError("cleanup_timeout", 503) from None
                except Exception:
                    raise DriverError("cleanup_failed", 503) from None
            return
        turn.cancelled = True
        session.closed = True
        if turn.task and not turn.task.done():
            turn.task.cancel()
            try:
                async with asyncio.timeout(max(0, cleanup_deadline - time.monotonic())):
                    await asyncio.shield(
                        asyncio.gather(turn.task, return_exceptions=True)
                    )
            except TimeoutError:
                raise DriverError("cleanup_timeout", 503) from None
        if session.cleanup_task:
            try:
                async with asyncio.timeout(max(0, cleanup_deadline - time.monotonic())):
                    await asyncio.shield(session.cleanup_task)
            except TimeoutError:
                raise DriverError("cleanup_timeout", 503) from None
            except Exception:
                raise DriverError("cleanup_failed", 503) from None

    async def close_session(self, session, cleanup_deadline):
        if (
            not isinstance(session, _Session)
            or session.owner is not self
            or session.loop is not asyncio.get_running_loop()
        ):
            raise DriverError("invalid_session", 409)
        if session.active:
            active = session.active
            if not active.finished:
                await self.cancel(active, "session_closed", cleanup_deadline)
            elif active.task and not active.task.done():
                try:
                    async with asyncio.timeout(
                        max(0, cleanup_deadline - time.monotonic())
                    ):
                        await asyncio.shield(
                            asyncio.gather(active.task, return_exceptions=True)
                        )
                except TimeoutError:
                    raise DriverError("cleanup_timeout", 503) from None
        if session.cleanup_task:
            try:
                async with asyncio.timeout(max(0, cleanup_deadline - time.monotonic())):
                    await asyncio.shield(session.cleanup_task)
            except TimeoutError:
                raise DriverError("cleanup_timeout", 503) from None
            except Exception:
                raise DriverError("cleanup_failed", 503) from None
        session.closed = True
