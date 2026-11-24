"""Antigravity ACP provider and OpenAI-compatible gateway."""

__version__ = "0.2.0.dev0"

from .provider import (
    AntigravityLLM,
    AntigravityRPCProvider,
    antigravity_rpc_provider,
)

__all__ = [
    "AntigravityLLM",
    "AntigravityRPCProvider",
    "antigravity_rpc_provider",
    "__version__",
]
