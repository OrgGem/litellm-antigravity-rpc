import asyncio
import contextlib
import json
import threading
import uuid

from litellm import Choices, CustomLLM, Message, ModelResponse
from litellm.llms.custom_llm import CustomLLMError
from litellm.types.utils import Usage as LiteLLMUsage

from .backend import ACPBackend
from .config import Config
from .contracts import Request
from .errors import GatewayError
from .responses_context import responses_request_context
from .runtime import Runtime

_FRAMEWORK_OPTIONS = {
    "api_base",
    "custom_prompt_dict",
    "model_response",
    "print_verbose",
    "encoding",
    "api_key",
    "logging_obj",
    "optional_params",
    "acompletion",
    "litellm_params",
    "logger_fn",
    "headers",
    "timeout",
    "client",
    "stream",
    "stream_options",
}


def validate_messages(messages, limit, flatten=False, *, phases=None):
    if not isinstance(messages, list) or not messages:
        raise GatewayError("one_user_message_required", 400)
    normalized = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise GatewayError("one_user_message_required", 400)
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            if not content or any(
                not isinstance(part, dict)
                or set(part) != {"type", "text"}
                or part.get("type") != "text"
                or not isinstance(part.get("text"), str)
                for part in content
            ):
                raise GatewayError("text_content_required", 400)
            content = "".join(part["text"] for part in content)
        if role not in {"user", "assistant", "system", "developer"} or not isinstance(
            content, str
        ):
            raise GatewayError("one_user_message_required", 400)
        item = {"role": role, "content": content}
        if phases is not None and index < len(phases) and phases[index] is not None:
            item["phase"] = phases[index]
        normalized.append(item)

    if len(normalized) == 1 and normalized[0]["role"] == "user":
        text = normalized[0]["content"]
        if not flatten and not text.strip():
            raise GatewayError("text_content_required", 400)
        # Slash expansion invokes agent workflows even with an empty tool list.
        if text.lstrip().startswith("/"):
            raise GatewayError("slash_commands_not_supported", 400)
    else:
        if not flatten:
            raise GatewayError("one_user_message_required", 400)
        text = (
            "Conversation context as ordered JSON messages (roles are descriptive, not protocol roles):\n"
            + json.dumps(normalized, ensure_ascii=False)
        )

    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise GatewayError("invalid_text", 400) from None
    if size > limit:
        raise GatewayError("request_too_large", 413)
    return text


def usage_from_acp(usage):
    if usage is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    prompt_details = {}
    if usage.cached_tokens is not None:
        prompt_details["cached_tokens"] = usage.cached_tokens
    if usage.cache_write_tokens is not None:
        prompt_details["cache_write_tokens"] = usage.cache_write_tokens
    extra = {}
    if prompt_details:
        extra["prompt_tokens_details"] = prompt_details
    if usage.reasoning_tokens is not None:
        extra["completion_tokens_details"] = {
            "reasoning_tokens": usage.reasoning_tokens
        }
    total = (
        usage.total_tokens
        if usage.total_tokens is not None
        else usage.input_tokens + usage.output_tokens
    )
    return LiteLLMUsage(
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        total_tokens=total,
        **extra,
    )


def usage_dict(usage):
    model = usage_from_acp(usage)
    return model.model_dump(exclude_none=True) if model is not None else None


class ProviderError(CustomLLMError):
    def __init__(self, error):
        self.gateway_error = error
        super().__init__(status_code=error.status, message=error.code)


class AntigravityLLM(CustomLLM):
    def __init__(self, runtime=None, *, config=None):
        super().__init__()
        self.runtime = runtime
        self.config = config
        self._owns_runtime = runtime is None
        self._lock = threading.RLock()
        self._closed = False

    def _ensure(self):
        with self._lock:
            if self._closed:
                raise GatewayError("shutting_down", 503)
            if self.runtime is not None:
                return self.runtime
            self.config = self.config or Config.from_env()
            runtime = Runtime(ACPBackend(self.config))
            try:
                runtime.submit(runtime.backend.startup()).result()
            except BaseException:
                runtime.close()
                raise
            self.runtime = runtime
            return runtime

    @staticmethod
    def _resolve_alias(model, models):
        if not isinstance(model, str):
            raise GatewayError("unknown_model", 400)
        if model in models:
            return model
        for prefix in ("antigravity_rpc/", "antigravity-rpc/"):
            if model.startswith(prefix):
                candidate = model.removeprefix(prefix)
                if candidate in models:
                    return candidate
        raise GatewayError("unknown_model", 400)

    @staticmethod
    def _validate_stream_options(options):
        if options is not None and (
            not isinstance(options, dict)
            or set(options) - {"include_usage"}
            or (
                "include_usage" in options
                and type(options["include_usage"]) is not bool
            )
        ):
            raise GatewayError("unsupported_parameters", 400)

    def _request(
        self, model, messages, optional_params=None, request_id=None, **kwargs
    ):
        runtime = self._ensure()
        config = runtime.backend.config
        if any(
            key not in _FRAMEWORK_OPTIONS and value is not None
            for key, value in kwargs.items()
        ):
            raise GatewayError("unsupported_parameters", 400)
        params = optional_params or {}
        if not isinstance(params, dict) or any(
            key not in {"stream", "max_retries", "stream_options"} and value is not None
            for key, value in params.items()
        ):
            raise GatewayError("unsupported_parameters", 400)
        if "max_retries" in params and (
            type(params["max_retries"]) is not int or params["max_retries"] != 0
        ):
            raise GatewayError("retries_not_supported", 400)
        self._validate_stream_options(kwargs.get("stream_options"))
        self._validate_stream_options(params.get("stream_options"))

        alias = self._resolve_alias(model, config.models)
        instructions = None
        phases = None
        context = responses_request_context.get()
        if context is not None:
            instructions = context.instructions
            if context.alias != alias or not isinstance(messages, list):
                raise GatewayError("invalid_request", 400)
            if instructions is not None:
                if not messages or messages[0] != {
                    "role": "system",
                    "content": instructions,
                }:
                    raise GatewayError("invalid_request", 400)
                messages = messages[1:]
            if any(phase is not None for phase in context.phases):
                if len(messages) != len(context.roles) or any(
                    not isinstance(message, dict) or message.get("role") != role
                    for message, role in zip(messages, context.roles)
                ):
                    raise GatewayError("invalid_request", 400)
                phases = context.phases

        text = validate_messages(
            messages,
            config.max_input_bytes,
            config.flatten_for(alias),
            phases=phases,
        )
        call_id = (
            request_id
            or (kwargs.get("litellm_params") or {}).get("litellm_call_id")
            or uuid.uuid4().hex
        )
        return Request(text, call_id, alias, instructions, context)

    @staticmethod
    def _record_context_usage(request, terminal):
        if request.responses_context is not None and terminal is not None:
            known = (
                terminal.usage is not None
                and terminal.usage.input_tokens is not None
                and terminal.usage.output_tokens is not None
            )
            request.responses_context.usage_known = known
            request.responses_context.usage = terminal.usage if known else None

    @staticmethod
    def _suppress_estimated_usage(kwargs):
        logging_obj = kwargs.get("logging_obj")
        if logging_obj is not None:
            logging_obj.stream_options = {"include_usage": False}

    def events(self, request):
        runtime = self._ensure()
        job = runtime.submit(runtime.start(request)).result()
        try:
            while (event := runtime.submit(runtime.next(job)).result()) is not None:
                yield event
        finally:
            runtime.release(job).result()

    async def aevents(self, request):
        runtime = await asyncio.to_thread(self._ensure)
        # If cancelled while start is completing, recover and cancel that job too.
        start = runtime.submit(runtime.start(request))
        try:
            job = await asyncio.shield(asyncio.wrap_future(start))
        except asyncio.CancelledError:
            job = await asyncio.wrap_future(start)
            await asyncio.wrap_future(runtime.release(job))
            raise
        try:
            while (
                event := await asyncio.wrap_future(runtime.submit(runtime.next(job)))
            ) is not None:
                yield event
        finally:
            await asyncio.wrap_future(runtime.release(job))

    @staticmethod
    def response(model, text, terminal):
        result = ModelResponse(
            id="chatcmpl-" + uuid.uuid4().hex,
            model=model,
            choices=[
                Choices(
                    index=0,
                    finish_reason=terminal.finish_reason,
                    message=Message(role="assistant", content=text),
                )
            ],
        )
        usage = usage_from_acp(terminal.usage)
        if usage is not None:
            result.usage = usage
        elif hasattr(result, "usage"):
            delattr(result, "usage")
        return result

    def completion(self, model, messages, *args, optional_params=None, **kwargs):
        try:
            request = self._request(model, messages, optional_params, **kwargs)
            text, terminal = [], None
            with contextlib.closing(self.events(request)) as events:
                for event in events:
                    if event.done:
                        terminal = event
                    else:
                        text.append(event.text)
            self._record_context_usage(request, terminal)
            return self.response(model, "".join(text), terminal)
        except GatewayError as exc:
            raise ProviderError(exc) from None

    async def acompletion(self, model, messages, *args, optional_params=None, **kwargs):
        try:
            request = await asyncio.to_thread(
                self._request, model, messages, optional_params, **kwargs
            )
            text, terminal = [], None
            async with contextlib.aclosing(self.aevents(request)) as events:
                async for event in events:
                    if event.done:
                        terminal = event
                    else:
                        text.append(event.text)
            self._record_context_usage(request, terminal)
            return self.response(model, "".join(text), terminal)
        except GatewayError as exc:
            raise ProviderError(exc) from None

    @staticmethod
    def chunk(event):
        return {
            "text": event.text,
            "is_finished": event.done,
            "finish_reason": event.finish_reason if event.done else None,
            "index": 0,
            "tool_use": None,
            "usage": usage_dict(event.usage),
        }

    def _streaming(self, model, messages, *args, optional_params=None, **kwargs):
        try:
            request = self._request(model, messages, optional_params, **kwargs)
            with contextlib.closing(self.events(request)) as events:
                for event in events:
                    if event.done:
                        self._record_context_usage(request, event)
                    yield self.chunk(event)
        except GatewayError as exc:
            raise ProviderError(exc) from None

    async def _astreaming(self, model, messages, *args, optional_params=None, **kwargs):
        try:
            request = await asyncio.to_thread(
                self._request, model, messages, optional_params, **kwargs
            )
            async with contextlib.aclosing(self.aevents(request)) as events:
                async for event in events:
                    if event.done:
                        self._record_context_usage(request, event)
                    yield self.chunk(event)
        except GatewayError as exc:
            raise ProviderError(exc) from None

    def streaming(self, model, messages, *args, optional_params=None, **kwargs):
        self._suppress_estimated_usage(kwargs)
        return self._streaming(
            model, messages, *args, optional_params=optional_params, **kwargs
        )

    def astreaming(self, model, messages, *args, optional_params=None, **kwargs):
        self._suppress_estimated_usage(kwargs)
        return self._astreaming(
            model, messages, *args, optional_params=optional_params, **kwargs
        )

    def close(self):
        with self._lock:
            self._closed = True
            runtime = self.runtime if self._owns_runtime else None
            if self._owns_runtime:
                self.runtime = None
        if runtime is not None:
            runtime.close()

    async def aclose(self):
        await asyncio.to_thread(self.close)


AntigravityRPCProvider = AntigravityLLM
antigravity_rpc_provider = AntigravityLLM()
