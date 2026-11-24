"""Provider-neutral contracts for the completion backend."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Request:
    text: str
    request_id: str
    model: str | None = None
    instructions: str | None = None
    responses_context: object | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Usage:
    """Per-request usage only; unknown counters must remain None."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class Event:
    text: str = ""
    done: bool = False
    usage: Usage | None = None
    finish_reason: str | None = None


class Backend(Protocol):
    def stream(self, request: Request) -> AsyncIterator[Event]: ...
    async def close(self) -> None: ...
