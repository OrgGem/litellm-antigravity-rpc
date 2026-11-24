# Antigravity native v2: guarded protocol foundation

`litellm_antigravity_rpc.native_v2.AntigravityNativeDriver` implements the seven
async operations of frozen `agent-rpc/0.1`. This foundation is separate from the
existing standalone text-only provider and does not enable a v2 HTTP/SDK route.

All v2 capability manifests are blocked/unverified. Production `open_session()`
fails before spawning a process. The private `_for_protocol_tests` constructor
only injects a synthetic ACP client; it is not a readiness or security gate.
The production path retains the `agy` **1.5.3** version + handshake checks; the
1.5.9 binary found on PATH during G0a is not silently accepted or updated.

The controlled fixture path starts an actual synthetic ACP subprocess, selects
the declared model, accepts one text user message, forwards only text updates,
returns explicit unknown usage, and cleans up the process. It rejects tools,
reasoning output/control, token limits, parallel tools, unsupported histories
and session reuse before spawning. Raw thought/plan notifications are not exposed.
Cleanup uses an independently shielded task: a deadline expiry is reported,
not treated as proof that cleanup completed. Sessions/turns have driver and loop
ownership, one active event consumer, and can detach/reattach without canceling.

ACP `tool_call` is an agent activity notification, not an OpenAI client tool
invocation. Permission responses are not tool results. A gateway-owned MCP bridge
must be independently proven, including exact correlation, native pause/resume,
profile/hook isolation, auth and cleanup, before any tool capability is opened.
Do not configure external MCP/shell/FS access from a request or assume event
observation prevents a tool's side effects.

Historical 1.5.3 prompt-result `inputTokens`/`outputTokens` observations do not
prove scope for all model calls, sessions, compaction or tool handoffs. This
candidate intentionally emits null/unknown instead of claiming complete counts.
`usage_update.used/size` is context-window data, not request token accounting.
No estimated spend, quota or cost is reported.

Frozen schema/semantics/fixtures are packaged under
`litellm_antigravity_rpc/contracts/agent-rpc/v0.1/`, contract hash
`749182d456eb363be2d63b2a98d66d0f1bf203b93d8a16ed4a8bdd90834e5fc1`.
Independent repo fixture tests use `uv sync --extra dev` and `python -m pytest`;
they do not replace version-specific Windows/Linux live acceptance.
