# ACP evidence, 2026-11-12

`live-*` là transcript từ agy **1.5.3**, chủ yếu Windows; `live-linux-auth-required`
được thu trong container Linux sạch. Client name thật `litellm-antigravity-rpc`.
Không giả danh editor khác để mở model.
`spec-*` là ví dụ tổng hợp từ tài liệu, **không phải bằng chứng live**.

| Fixture | Bằng chứng |
|---|---|
| live-isolated | Initialize, new, model config, idle cancel, close |
| live-absolute-app-dir-error | `--app-data-dir` tuyệt đối gây lỗi -32603 |
| live-isolated-prompt | Text chunk, end_turn, prompt result usage; model sai -32602 |
| live-fresh-state | Đường dẫn gemini tuyệt đối vẫn dùng được đăng nhập hệ điều hành |
| live-cancel | Prompt được gửi, cancel sau 200 ms, kết quả cancelled |
| live-tool-attempt | Agent tools rỗng trả TOOLS_UNAVAILABLE; không tạo marker |
| live-retention | Đóng rồi load lại chính session vừa tạo thành công |
| live-customization-canary | Cả 4 hook/MCP canary workspace/global đều khởi động |
| live-clean-profile | Không canary ngoài profile nào khởi động; hai prompt cùng session có usage riêng |
| live-permission | Server yêu cầu quyền run_command; client chọn deny/reject_once; không có marker |
| live-linux-auth-required | session/new trả -32000 trên Linux không mạng/credential/keyring |
| spec-auth-required | Ví dụ theo tài liệu; đối chiếu live-linux-auth-required |
| spec-permission-denied | Ví dụ protocol tối giản; xem live-permission để đối chiếu agent thật |

Các probe đầu được ghi trước khi harness bổ sung tùy chọn; không phải mọi fixture
đều có tất cả metadata. `captureDate` là ngày thực hiện. Session ID, cwd, title,
metadata extension, raw tool payload và thought text đã được loại/đổi placeholder.
Text còn lại là prompt kiểm thử cố định và câu trả lời ngắn. Không lưu stderr,
log CLI hoặc credential. Do đã sanitize, fixture không phải bản raw để kiểm định
toàn bộ schema `_meta`.

Tên `isolated` chỉ phản ánh **thử** cách ly môi trường con. Nó không có nghĩa
không đăng nhập: Windows Credential Manager vẫn có thể được CLI sử dụng.
`outcome` mô tả đường chạy harness, không phải kết luận mọi assertion đều đạt.
Xem [protocol matrix](../../../docs/PROTOCOL_MATRIX.md) trước khi dùng fixture.

Tái chạy probe không prompt:

```powershell
.\.venv\Scripts\python.exe scripts\probe_acp_contract.py --output .local\probe.json
```

Các cờ `--prompt-probe`, `--cancel-probe`, `--tool-probe`, `--canary-probe`
và `--outside-canary`, `--usage-probe`, `--permission-probe` gửi prompt và có thể
tiêu thụ quota. `--retention-probe` load lại chính session
vừa tạo; không liệt kê hoặc đọc hội thoại có sẵn. `--canary-probe` cố ý đặt
hook/MCP thử nghiệm chỉ tạo marker trong thư mục tạm. Không dùng như profile
production. Harness không đọc, sao chép hay xóa credential, không gọi logout.
