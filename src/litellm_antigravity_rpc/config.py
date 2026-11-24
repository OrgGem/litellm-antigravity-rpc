import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .errors import GatewayError

_CLI_VERSION_RE = re.compile(r"(?:^|\s)1\.5\.(\d+)(?:[-+][A-Za-z0-9.]+)?\s*\Z")
_LIMIT_FIELDS = (
    "timeout",
    "cleanup_timeout",
    "concurrency",
    "max_pending",
    "max_input_bytes",
    "max_output_bytes",
    "queue_size",
    "frame_limit",
)


def is_supported_cli_version(raw: str | None) -> bool:
    """Accept reviewed Antigravity ACP 1.5.x builds from 1.5.3 onward."""
    if not isinstance(raw, str):
        return False
    match = _CLI_VERSION_RE.search(raw.strip())
    return match is not None and int(match.group(1)) >= 3


def parse_models_object(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Model map must be a nonempty object")
    result = {}
    for alias, spec in value.items():
        if spec is None or isinstance(spec, str):
            result[alias] = Model(model=spec)
        elif isinstance(spec, dict) and not (
            set(spec) - {"model", "thought_level", "flatten"}
        ):
            result[alias] = Model(**spec)
        else:
            raise ValueError("Invalid model map entry")
    return result


@dataclass(frozen=True)
class Model:
    model: str | None = "gemini-4-carbon"
    thought_level: str | None = None
    flatten: bool | None = None


@dataclass(frozen=True)
class Config:
    models: dict[str, Model] = field(
        default_factory=lambda: {"antigravity": Model("gemini-4-carbon", "medium")}
    )
    executable: str = "agy"
    timeout: float = 120
    concurrency: int = 2
    max_pending: int = 16
    max_input_bytes: int = 128 * 1024
    max_output_bytes: int = 2 * 1024 * 1024
    queue_size: int = 64
    frame_limit: int = 1024 * 1024
    cleanup_timeout: float = 3
    flatten: bool = False
    flatten_by_alias: dict[str, bool] = field(default_factory=dict)
    api_key: str = field(default="", repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.models, dict)
            or not self.models
            or any(
                not isinstance(alias, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", alias)
                or not isinstance(model, Model)
                or (
                    model.model is not None
                    and (
                        not isinstance(model.model, str)
                        or not model.model
                        or len(model.model) > 200
                    )
                )
                or (
                    model.thought_level is not None
                    and (
                        not isinstance(model.thought_level, str)
                        or not model.thought_level
                        or len(model.thought_level) > 200
                    )
                )
                or (model.flatten is not None and type(model.flatten) is not bool)
                for alias, model in self.models.items()
            )
        ):
            raise ValueError("Invalid model aliases")
        if (
            not isinstance(self.executable, str)
            or not self.executable.strip()
            or "\0" in self.executable
        ):
            raise ValueError("Invalid executable")
        if type(self.flatten) is not bool:
            raise ValueError("Invalid flatten flag")
        if not isinstance(self.flatten_by_alias, dict) or any(
            not isinstance(k, str) or type(v) is not bool
            for k, v in self.flatten_by_alias.items()
        ):
            raise ValueError("Invalid flatten_by_alias")
        for name in ("timeout", "cleanup_timeout"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"Invalid {name}")
        for name in (
            "concurrency",
            "max_pending",
            "max_input_bytes",
            "max_output_bytes",
            "queue_size",
            "frame_limit",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"Invalid {name}")

    def flatten_for(self, alias: str) -> bool:
        spec = self.models.get(alias)
        if spec is not None and spec.flatten is not None:
            return spec.flatten
        return self.flatten_by_alias.get(alias, self.flatten)

    def resolve_executable(self):
        path = shutil.which(self.executable)
        if not path:
            raise GatewayError("cli_not_found", 503)
        return str(Path(path).resolve())

    @classmethod
    def from_env(cls):
        def boolean(name, default):
            value = os.getenv(name, default).lower()
            if value not in {"true", "false"}:
                raise GatewayError("invalid_configuration", 400)
            return value == "true"

        try:
            raw_models = os.getenv(
                "ANTIGRAVITY_MODEL_MAP",
                '{"antigravity":{"model":"gemini-4-carbon","thought_level":"medium"}}',
            )
            models = parse_models_object(json.loads(raw_models))
            limits = json.loads(os.getenv("ANTIGRAVITY_RPC_LIMITS", "{}"))
            if not isinstance(limits, dict) or set(limits) - set(_LIMIT_FIELDS):
                raise ValueError("Invalid ANTIGRAVITY_RPC_LIMITS")
            kwargs = {
                "models": models,
                "executable": os.getenv("ANTIGRAVITY_RPC_EXECUTABLE", "agy"),
                "timeout": float(
                    os.getenv(
                        "ANTIGRAVITY_RPC_REQUEST_TIMEOUT",
                        str(limits.get("timeout", 120)),
                    )
                ),
                "cleanup_timeout": float(
                    os.getenv(
                        "ANTIGRAVITY_RPC_CLEANUP_TIMEOUT",
                        str(limits.get("cleanup_timeout", 3)),
                    )
                ),
                "concurrency": int(
                    os.getenv(
                        "ANTIGRAVITY_RPC_MAX_CONCURRENT",
                        str(limits.get("concurrency", 2)),
                    )
                ),
                "max_pending": int(
                    os.getenv(
                        "ANTIGRAVITY_RPC_MAX_PENDING",
                        str(limits.get("max_pending", 16)),
                    )
                ),
                "max_input_bytes": int(
                    os.getenv(
                        "ANTIGRAVITY_RPC_MAX_INPUT_BYTES",
                        str(limits.get("max_input_bytes", 128 * 1024)),
                    )
                ),
                "max_output_bytes": int(
                    os.getenv(
                        "ANTIGRAVITY_RPC_MAX_OUTPUT_BYTES",
                        str(limits.get("max_output_bytes", 2 * 1024 * 1024)),
                    )
                ),
                "queue_size": int(
                    os.getenv(
                        "ANTIGRAVITY_RPC_QUEUE_SIZE",
                        str(limits.get("queue_size", 64)),
                    )
                ),
                "frame_limit": int(
                    os.getenv(
                        "ANTIGRAVITY_RPC_FRAME_LIMIT",
                        str(limits.get("frame_limit", 1024 * 1024)),
                    )
                ),
                "flatten": boolean("ANTIGRAVITY_RPC_ALLOW_PROMPT_FLATTENING", "false"),
                "api_key": os.getenv("ANTIGRAVITY_RPC_API_KEY")
                or os.getenv("LITELLM_MASTER_KEY", ""),
            }
            return cls(**kwargs)
        except GatewayError:
            raise
        except (ValueError, TypeError):
            raise GatewayError("invalid_configuration", 400) from None


def load_models(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_models_object(value)
