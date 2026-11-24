# Kết quả kiểm tra

> Phần khởi tạo và Bước 1 là mốc lịch sử. Kết quả backend/API MVP mới nhất nằm ở cuối file.

Ngày kiểm tra: 2026-11-12, môi trường Windows, múi giờ Asia/Bangkok.

## Môi trường

| Thành phần | Giá trị |
|---|---|
| Python | 3.13.12 |
| Antigravity CLI | 1.5.3 |
| Package | 0.1.0.dev0, cài editable trong `.venv` |
| Ruff | 0.17.4 |
| Build | 1.6.1 |
| Hatchling dùng khi build | 1.33.0 |
| Git | Repository đã tồn tại, chưa có commit; nhánh main |

## Kết quả

| Kiểm tra | Kết quả |
|---|---|
| `agy --version` và `agy acp --help` | Có executable và ACP stdio server |
| `python -m unittest discover -s tests -v` | 4 tests đạt: handshake, invalid JSON, remote error, timeout |
| `ruff check src tests` | Đạt |
| `ruff format --check src tests` | Đạt |
| `python -m pip check` | Không có dependency bị lỗi |
| `python -m build` | Tạo sdist và wheel |
| `antigravity-rpc --version` | 0.1.0.dev0 |
| ACP initialize với CLI thật | Thành công, protocolVersion 1 |

## Capability quan sát được

Server nhận diện `Antigravity ACP Server`, version `1.5.3`. Initialize công bố:

- `loadSession: true`.
- Prompt: image và embeddedContext được hỗ trợ; audio không được hỗ trợ.
- Session: resume, close, load, list và additionalDirectories.
- Auth method IDs: `oauth-personal`, `oauth-business`, `gemini-api-key`, `agent-platform`.
- Extension metadata công bố khả năng append system prompt; chưa thử và chưa coi là tương thích system role OpenAI.

Danh sách capability chỉ là dữ liệu server công bố; bootstrap chưa triển khai các chức năng này. Không thu thập hoặc lưu token, prompt hoặc nội dung hội thoại.

## Giới hạn bằng chứng

Probe chỉ gửi `initialize` từ thư mục tạm và đóng subprocess. Chưa tạo session hoặc gửi prompt. Authentication, entitlement, chọn model, token usage, quyền tool, retention, concurrency, Windows process-tree cleanup cho tác vụ agent và OpenAI API đều chưa được kiểm chứng.

Chưa có API host hoạt động hoặc kiểm thử Linux. Chưa commit, push hoặc thay đổi remote Git. Bước tiếp theo là bước 1 trong [kế hoạch](PLAN.md).

## Bổ sung Bước 1 — 2026-11-12

- Windows/Python 3.13.12: **8/8 unittest đạt**; lint và format đạt cho src/tests/scripts;
  pip check đạt. Đã sửa `authMethods: null` gây TypeError, JSON-RPC boolean ID bị
  chấp nhận như số 1, và timeout NaN/infinity.
- Build mặc định có isolation đạt với Hatchling 1.33.0, tạo sdist/wheel; lock check đạt.
  Build `--no-isolation` cần cài sẵn Hatchling (môi trường dev hiện không có);
  dùng `python -m build` như hướng dẫn. Fixture JSON đã kiểm tra dữ liệu nhạy cảm.
- Docker Linux/Python 3.12: **8/8 unittest đạt**, chạy không mạng và mount source
  read-only. Đây là test subprocess giả và replay fixture, không phải CLI Linux live.
- LiteLLM **1.109.0**, OpenAI SDK **3.49.0**: bốn đường gọi sync/async và stream/
  non-stream đạt trên Windows và Linux; xem [spike](LITELLM_SPIKE.md).
- agy **1.5.3** Windows live: initialize/new/config/invalid-model/text/end_turn/
  usage/cancel/close/load được ghi vào [fixture](../tests/fixtures/acp/README.md).
  Các lượt model dùng prompt tổng hợp và đã tiêu thụ quota của login CLI hiện có.
- Positive control: agent tools rỗng nhưng **4/4 canary hook/MCP trong cấu hình
  được chọn vẫn khởi động**. Profile sửa dùng workspace/gemini-dir riêng: **0/6
  canary bên ngoài chạy**, hai prompt text thành công.
- Permission live: server yêu cầu run_command, client chọn deny/reject_once;
  không có marker. Usage hai lượt có outputTokens riêng 39/19; cache details
  chưa ánh xạ. Chỉ input/output của terminal prompt result được chốt cho MVP.
- Auth-required live: đúng agy 1.5.3 Linux (archive SHA-256 đã kiểm), container
  không mạng/credential/keyring; session/new trả **-32000 Authentication required**.
- Retention bền vững, race cancellation, cache semantics và process tree
  production chưa nghiệm thu; thuộc giới hạn được ghi rõ cho bước triển khai.

**Bước 1 hoàn tất trong phạm vi matrix.** Xem [matrix](PROTOCOL_MATRIX.md),
[profile riêng](RESTRICTED_PROFILE.md) và checklist cập nhật trong [plan](PLAN.md).
Chưa xây backend, chưa commit/push, chưa có API host.

## Bổ sung MVP Bước 2–4 — 2026-11-12

Đã triển khai backend/client ACP, profile riêng, process tree cleanup, runtime
chung sync/async, CustomLLM adapter, API host và lệnh `serve`. Các dòng “chưa xây
backend” ở trên mô tả mốc lịch sử trước đợt này.

### Môi trường và kết quả

| Kiểm tra | Kết quả |
|---|---|
| Windows Python 3.13.12, unittest | **27/27 đạt**, 9.293 giây ở lượt cuối |
| Linux Docker Python 3.12, unittest | **27/27 đạt**, 7.705 giây; network none, source read-only |
| LiteLLM | 1.109.0; public completion/acompletion/streaming/astreaming đều đạt với fake subprocess |
| OpenAI SDK | 3.49.0; fake API integration và localhost HTTP live đạt |
| CLI thật Windows | agy 1.5.3; alias antigravity → gemini-4-carbon, thought_level medium |
| Live non-stream | Nhận đúng ACP_BACKEND_OK; có usage |
| Live SSE | Nhận đúng ACP_STREAM_OK; finish_reason stop |
| Live disconnect | Đóng stream sau content đầu; runtime jobs về 0 trong hạn kiểm tra |
| Unknown alias | HTTP 400 trước model call |
| CLI thật Linux | Backend startup trả upstream_authentication_required; container không mạng/credential, không prompt |
| Ruff | Lint và format đạt cho src/tests/scripts |
| Dependency/lock | uv lock --check và uv pip check đạt |
| Build | Sdist/wheel đạt, build isolation với Hatchling 1.33.0 |

Linux dùng image nền `python:3.12-slim`, digest
`sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`.
Dependency được cài theo `requirements-runtime.lock` có hash vào image test
cục bộ, sau đó tests chạy với `--network none`. CLI Linux là bản 1.5.3 đã kiểm
SHA-256 ở Bước 1; không tải credential của Windows vào container.

### Phạm vi tests

- 8 doctor tests: handshake, fixture và các lỗi giao thức/timeout.
- 10 backend tests: text/usage, process mới, malformed/oversize/EOF/ID/auth/tool,
  permission deny, lỗi sau text, timeout kill descendants, queue deadline,
  slow consumer, alias sai, profile allowlist và đóng generator sớm.
- 4 API tests: OpenAI SDK, auth/validation, disconnect trước content và lỗi
  giữa SSE không có `[DONE]` hay upstream message thô.
- 1 provider test: bốn đường public LiteLLM.
- 4 runtime tests: admission/concurrency, cancel khi đợi, backpressure cleanup,
  shutdown giữa readiness và shutdown khi consumer còn chạy.

Review đã sửa race gắn Windows Job Object, cleanup async generator lồng nhau,
shutdown ownership và tương thích max_retries=0 của LiteLLM. Xem
[CODE_REVIEW](CODE_REVIEW.md) để biết nguyên nhân và bằng chứng hồi quy.

### Tái hiện

```powershell
uv sync --extra dev --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
uv lock --check
uv pip check
uv run --frozen python -m build
# Opt-in: cần login chính thức, gửi prompt và dùng quota.
uv run --frozen python scripts/smoke_live.py
```

Live smoke khởi động server trên localhost port tạm, gửi prompt tổng hợp, đóng
server/runtime khi kết thúc. Không lưu key, token, raw stderr hoặc câu trả lời
vào fixture. Log kết quả cục bộ nằm dưới `.local/` và bị Git ignore.

### Chưa kiểm chứng

CI GitHub Actions mới được tạo, chưa chạy remote; chưa chạy Python 3.11 ở máy
này. Chưa có Linux authenticated model E2E, production deployment, benchmark
throughput, soak/load dài hạn, retention phía dịch vụ hoặc crash recovery.
Profile không phải OS sandbox. Readiness không xác nhận quyền/quota model.
Chưa commit/push; không để server smoke chạy nền sau nghiệm thu.
