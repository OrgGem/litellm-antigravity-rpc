# Hợp đồng ACP đã kiểm chứng

Ngày: 2026-11-12 (Asia/Bangkok). CLI **1.5.3**, Windows/Python 3.13.12;
auth-required kiểm tra thêm trên Linux/container sạch với Python 3.12.
Phạm vi: hoàn tất nghiên cứu Bước 1, chưa có backend production.

## Kết quả và quyết định

| Hạng mục | Quan sát trực tiếp | Quyết định cho backend sau này |
|---|---|---|
| Transport | JSON-RPC 2.0, UTF-8 NDJSON qua stdio; protocolVersion 1 | Phân biệt request/response/notification; không chờ response cho notification |
| Initialize | `agentCapabilities`, `agentInfo`, 4 auth methods; có cả alias snake_case | Dùng camelCase đã quan sát; kiểm tra kiểu dữ liệu; không coi handshake là login |
| Session | `session/new` nhận cwd tuyệt đối và `mcpServers: []` | Session mới mỗi request, cwd do gateway quản lý |
| Model config | `model`, `thought_level`, `mode` đều là select | Đọc option/value từ session, không hard-code danh sách entitlement |
| Model ID | Config `gemini-4-carbon`; legacy `gemini-4-carbon-medium` | Ưu tiên configOptions; không trộn hai hệ ID |
| Set model | Đặt lại giá trị hiện tại thành công; nhận config options | Đối chiếu currentValue trả về; đổi sang model khác chưa thử |
| Model không tồn tại | `session/set_config_option` trả -32602 | Validate alias/value trước prompt; không silently fallback |
| Text | `session/update` → `agent_message_chunk`, `content.type=text` | Chỉ xuất text của assistant; user/thought/plan/tool không phải answer |
| Completion | Response của `session/prompt`: `stopReason=end_turn` | Chỉ xác nhận thành công khi có terminal response |
| Cancel | Cancel sau 200 ms, response prompt `stopReason=cancelled` | Có evidence hủy lượt đang chờ; chưa kiểm race sau chunk/tool đang chạy |
| Idle cancel | Notification không có response; close tiếp theo thành công | Không coi đây là bằng chứng active cancellation |
| Usage | Nằm trên prompt result; hai lượt cùng session: output 39 rồi 19, input 2076 rồi 2194 | Output không cộng dồn; chỉ ánh xạ counters trên prompt result, không lấy notification context usage |
| Permission | Live request ID chuỗi `"1"`, tool run_command; chọn deny/reject_once, không tạo file | Server request ID là opaque, không được ép thành int; chọn reject_once theo kind, không đoán tên option |
| Close/retention | `session/close` → `{}`; `session/load` cùng ID sau close thành công | Close **không xóa** session; không tuyên bố ephemeral |
| Auth-required | Linux/container sạch trả session/new error -32000, Authentication required | Phân loại lỗi auth riêng; không chạy authenticate/login trong HTTP request |
| App-data-dir | Đường dẫn tuyệt đối làm session/new lỗi -32603, “must not be absolute” | Dùng tên tương đối cho cờ này ở 1.5.3 |
| Gemini-dir | Chấp nhận đường dẫn tuyệt đối; canary global tại đó được nạp | Có thể chọn cây cấu hình riêng; không đồng nghĩa cách ly OS keyring |

Model thật được gọi thành công là model mặc định session công bố
`gemini-4-carbon` với thought level `medium`. Các model khác trong danh sách
chưa được gọi. Listing không chứng minh entitlement; không đổi clientInfo để
giả danh editor. Những lượt thử đều dùng prompt tổng hợp, không gửi mã nguồn repo.

## Kiểm tra profile và sửa cách cách ly

Profile thử có agent Markdown `tools: []`, `mcpServers: []`, `skills: []`,
`plugins: []`, `subagent: false`; CLI dùng
`--include-default-customizations=false`, client không công bố filesystem hoặc
terminal, session/new không truyền MCP. Khi yêu cầu tạo file, agent trả
`TOOLS_UNAVAILABLE`, không thấy tool notification và không có file marker.

Nhưng khi cố ý đặt canary vào `.agents/hooks.json`, `.agents/mcp_config.json`
và `<gemini-dir>/config/` tương ứng, **cả bốn canary đều chạy**. Các hook chỉ
tạo marker; MCP canary tạo marker rồi thoát. Đây là bằng chứng khởi động process,
không phải MCP handshake/tool call thành công. `tools: []` không ngăn hook và
MCP process được nạp từ cấu hình. Những cờ trên không đủ làm ranh giới cách ly.

Sau đó đã thử workspace riêng chỉ chứa agent và gemini-dir tuyệt đối rỗng:
6 canary bên ngoài cây được chọn (HOME, USERPROFILE, thư mục cha) không chạy;
hai lượt text hoàn tất. [Profile cụ thể](RESTRICTED_PROFILE.md) ghi cách tạo cây,
giới hạn và yêu cầu fail-closed cho backend. Không thay cấu hình Antigravity cá
nhân. OS keyring vẫn được CLI truy cập theo cơ chế đăng nhập chính thức.

## Retention và usage

Close rồi load thành công chứng minh session còn khả dụng trong cùng process;
chưa đo thời gian lưu trên đĩa, retention phía dịch vụ hoặc load sau restart.
Initialize không công bố delete trong sessionCapabilities. Không tự gọi method
delete phỏng đoán hoặc xóa dữ liệu CLI. MVP phải công bố rõ session mới không
đồng nghĩa không lưu dữ liệu.

Hai response ngắn có usage khác nhau (input 2083/2088, output 9), còn lượt
tool-attempt có input 2112, output 126, thought 118, total 2238. Đây là số liệu
quan sát. Probe hai lượt cùng session cho outputTokens 39 rồi 19 (không cộng
dồn), thoughtTokens 30 rồi 12. Chốt ánh xạ giới hạn cho Bước 2: lấy inputTokens
và outputTokens từ terminal response của đúng prompt, chỉ nhận integer không âm;
thiếu thì unknown. Không cộng thoughtTokens vào outputTokens lần nữa. Cache
details và context-usage notification chưa được ánh xạ; không tính token theo
độ dài text hoặc suy diễn chi phí tiền.

## Nguồn và cách tái hiện

- [Fixture và provenance](../tests/fixtures/acp/README.md), [harness](../scripts/probe_acp_contract.py).
- [ACP của Antigravity](https://antigravity.google/docs/ide/extensions/acp/): session, config, auth và permissions.
- [Đăng nhập keyring](https://antigravity.google/docs/install?tab=cli): giải thích vì sao đổi HOME không làm mất login.
- [ACP config options](https://agentclientprotocol.com/protocol/v1/session-config-options), [prompt turn](https://agentclientprotocol.com/protocol/v1/prompt-turn).
- [Custom agent](https://antigravity.google/docs/subagents/), [discovery](https://antigravity.google/docs/customization-discovery/), [hooks](https://antigravity.google/docs/hooks/), [MCP](https://antigravity.google/docs/mcp/).

Tài liệu upstream được đối chiếu cùng ngày; quan sát live 1.5.3 có ưu tiên khi
đánh giá hành vi của binary đã cài. Updater Linux trả 1.5.6, nên probe Linux
dùng archive **1.5.3** từ [release chính thức](https://github.com/google-antigravity/antigravity-cli/releases/tag/1.5.3),
asset `agy_cli_linux_x64.tar.gz`, kiểm SHA-256 theo digest của GitHub API:
`1b4850599cce73a17f94ffb422df3bf9531531b77dbddc40c1a435e0c2c16807`.
Container không mạng, không mount credential/keyring; initialize thành công,
session/new trả -32000. CLI Windows không bị nâng cấp.
