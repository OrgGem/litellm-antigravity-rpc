# Profile text-only đã thử trên Windows

Ngày 2026-11-12, agy 1.5.3. Phần nghiên cứu dưới đây là cơ sở cho profile
backend hiện tại; khác biệt triển khai được ghi ở cuối file.

## Cây thư mục và lệnh

Mỗi request phải có root riêng do gateway tạo, không cho caller chọn cwd:

```text
<root>/
  workspace/.agents/agents/contract-text-only.md
  gemini/                  # Ban đầu rỗng; tuyệt đối không tái dùng cấu hình cá nhân
  logs/                    # Log CLI nội bộ, không xuất ra client/repository
```

Agent Markdown đã thử:

```yaml
---
name: contract-text-only
description: Text-only protocol probe
tools: []
mainAgent: true
subagent: false
mcpServers: []
skills: []
plugins: []
commandExecutionPolicy: off
---
Respond with plain text only. Never invoke tools.
```

Khởi động bằng argv trực tiếp, cwd là workspace:

```text
agy acp --agent contract-text-only
  --gemini-dir <root>/gemini
  --app-data-dir contract-probe
  --include-default-customizations=false
  --disable-telemetry
  --log-file <root>/logs/acp.log
```

Các dòng trên là mô tả argv, không phải lệnh shell nhiều dòng. `gemini-dir` phải
là đường dẫn tuyệt đối; `app-data-dir` phải là tên tương đối ở phiên bản 1.5.3.
Không truyền `--add-dir`, `--ide-mcp-port` hoặc skip-permissions. Client công bố
capabilities rỗng và session/new gửi `mcpServers: []`.

Root phải nằm ngoài checkout có cấu hình người dùng; workspace không chứa Git
root, symlink/junction hoặc file ngoài allowlist agent. Không copy hooks,
manifest discovery, plugin, MCP config hoặc credential vào cây này. Khi triển
khai backend, phải kiểm tra cây cấu hình và executable/version trước spawn;
không chỉ dựa vào cờ include-default-customizations.

## Bằng chứng

1. Positive control: đặt hook/MCP canary trong workspace và gemini-dir được chọn.
   Cả bốn khởi động, chứng minh flags và tools rỗng **không đủ** chặn discovery.
2. Clean profile: cây gemini-dir riêng rỗng, workspace chỉ có agent; đặt sáu
   hook/MCP canary ngoài cây này (HOME, USERPROFILE và thư mục cha). Không canary
   nào khởi động, hai prompt text hoàn tất. Fixture `live-clean-profile.json`.
3. Tool attempt với agent tools rỗng: không có tool notification/marker;
   trả `TOOLS_UNAVAILABLE`. Đây là quan sát của lượt thử, không phải chứng minh
   mọi hành vi của CLI hoặc mọi prompt tương lai.
4. Permission test riêng: cho phép duy nhất `run_command` trong agent thử và
   đặt `toolPermission: strict` trong profile tạm. Server gửi permission request
   với ID chuỗi `"1"`; client chọn option `deny` có kind `reject_once`. Không
   tạo file. Fixture `live-permission.json`. Profile thử permission **không**
   được dùng cho provider text-only.

Không thay cấu hình cá nhân. CLI vẫn dùng OS keyring chính thức; tách cấu hình
không tách danh tính đăng nhập. Close không xóa session. Profile cần được backend
thực thi và kiểm tra lại trong E2E; harness nghiên cứu không thay thế các kiểm
tra concurrency, race và process tree.

Tái hiện hai kiểm tra bổ sung (có dùng quota):

```powershell
.\.venv\Scripts\python.exe scripts\probe_acp_contract.py --outside-canary --usage-probe --output .local\clean-profile.json
.\.venv\Scripts\python.exe scripts\probe_acp_contract.py --permission-probe --output .local\permission.json
```

Nguồn thiết kế: [custom agent](https://antigravity.google/docs/subagents/),
[discovery](https://antigravity.google/docs/customization-discovery/),
[permission protocol](https://agentclientprotocol.com/protocol/v1/tool-calls).

## Profile backend hiện tại

`profile.py` thực thi cây riêng cho mỗi request; thêm thư mục `.git` rỗng trong
allowlist để tạo mốc discovery. Không dùng Git repository của người dùng.
HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP đều trỏ vào thư mục riêng; chỉ
giữ các biến hệ thống tối thiểu cho process. Không kế thừa key/token/proxy hoặc
biến cấu hình agent từ parent environment. Nếu cần proxy doanh nghiệp phải
thiết kế allowlist riêng, chưa có tùy chọn truyền tùy ý ở MVP.

Backend bỏ stderr và dùng `--log-file os.devnull`; không tạo logs/acp.log như
harness nghiên cứu. Trước spawn, từ chối symlink/junction, file ngoài allowlist
và gemini-dir không rỗng. Không cho HTTP caller sửa cwd, command hoặc agent.
CLI version phải đúng 1.5.3 cả khi startup và initialize từng request.

Smoke HTTP thật Windows đã chạy text/stream/cancel qua profile này; test fake
kiểm tra allowlist và cleanup cây process. Canary research vẫn là bằng chứng
cho các vị trí cấu hình đã thử, không chứng minh CLI là sandbox hay mọi discovery
path trong tương lai đều an toàn. Cây tạm của gateway được xóa sau khi process
dừng; không cam kết xóa retention phía dịch vụ/OS keyring.
