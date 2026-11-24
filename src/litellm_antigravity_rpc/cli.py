import argparse
import asyncio
import json
import math
import os
import secrets
import shutil

from . import __version__
from .doctor import ProbeError, probe
from .errors import GatewayError


def build_parser():
    parser = argparse.ArgumentParser(description="Antigravity ACP text gateway")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor", help="Probe ACP initialization without a model call"
    )
    doctor.add_argument("--agy-bin", default=None)
    doctor.add_argument("--timeout", type=float, default=15)
    serve = commands.add_parser("serve", help="Serve the local OpenAI-compatible API")
    serve.add_argument("--models", default=None, help="JSON model alias map file")
    serve.add_argument("--model", default=None, help="Antigravity model ID")
    serve.add_argument(
        "--model-name", default=None, help="API model alias (default: antigravity)"
    )
    serve.add_argument(
        "--thought-level", default=None, help="Optional ACP thought level"
    )
    serve.add_argument("--agy-bin", default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4000)
    serve.add_argument("--timeout", type=float, default=None)
    serve.add_argument("--concurrency", type=int, default=None)
    serve.add_argument(
        "--allow-prompt-flattening",
        action="store_true",
        help="Accept chat history and system/developer roles as ordered JSON text",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("--timeout must be finite and positive")
        binary = args.agy_bin or os.environ.get("ANTIGRAVITY_RPC_EXECUTABLE", "agy")
        executable = shutil.which(binary)
        if not executable:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": "Antigravity CLI executable not found.",
                    }
                )
            )
            return 1
        try:
            result = asyncio.run(probe([executable, "acp"], args.timeout))
        except (ProbeError, OSError, TimeoutError, ValueError):
            # CLI output intentionally excludes raw server messages and local paths.
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": "ACP initialization failed or timed out.",
                    }
                )
            )
            return 1
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.timeout is not None and (
        not math.isfinite(args.timeout) or args.timeout <= 0
    ):
        parser.error("--timeout must be finite and positive")
    if args.concurrency is not None and args.concurrency <= 0:
        parser.error("--concurrency must be positive")

    from .config import Config, Model, load_models

    try:
        base = Config.from_env()
        if args.models is not None:
            models = load_models(args.models)
        elif (
            args.model is not None
            or args.model_name is not None
            or args.thought_level is not None
        ):
            models = {
                args.model_name or "antigravity": Model(
                    model=args.model if args.model is not None else "gemini-4-carbon",
                    thought_level=args.thought_level,
                )
            }
        else:
            models = base.models

        raw_key = os.environ.get("ANTIGRAVITY_RPC_API_KEY") or os.environ.get(
            "LITELLM_MASTER_KEY"
        )
        generated = raw_key is None
        if generated:
            raw_key = "sk-" + secrets.token_urlsafe(32)
        elif len(raw_key) < 16 or not raw_key.isascii():
            parser.error(
                "Set ANTIGRAVITY_RPC_API_KEY or LITELLM_MASTER_KEY to at least 16 ASCII characters"
            )

        config = Config(
            models=models,
            executable=args.agy_bin or base.executable,
            api_key=raw_key,
            timeout=args.timeout if args.timeout is not None else base.timeout,
            concurrency=args.concurrency
            if args.concurrency is not None
            else base.concurrency,
            max_pending=base.max_pending,
            max_input_bytes=base.max_input_bytes,
            max_output_bytes=base.max_output_bytes,
            queue_size=base.queue_size,
            frame_limit=base.frame_limit,
            cleanup_timeout=base.cleanup_timeout,
            flatten=args.allow_prompt_flattening or base.flatten,
        )
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
        import uvicorn

        from .app import App
        from .backend import ACPBackend
        from .runtime import Runtime

        runtime = Runtime(ACPBackend(config))
        try:
            host = f"[{args.host}]" if ":" in args.host else args.host
            print(f"OpenAI base URL: http://{host}:{args.port}/v1", flush=True)
            print("Models: " + ", ".join(config.models), flush=True)
            if generated:
                print(f"Session API key: {raw_key}", flush=True)
            uvicorn.run(
                App(runtime),
                host=args.host,
                port=args.port,
                access_log=False,
                lifespan="on",
                timeout_graceful_shutdown=10,
                loop="asyncio",
            )
        finally:
            runtime.close()
    except (GatewayError, ValueError, TypeError, OSError):
        print(json.dumps({"status": "error", "error": "Invalid server configuration"}))
        return 1
    return 0
