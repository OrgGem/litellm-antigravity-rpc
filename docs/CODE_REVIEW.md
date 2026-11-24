# Review code MVP — 2026-11-12

Review thủ công mã nguồn và kiểm thử hồi quy trong cùng đợt triển khai, không
phải review độc lập hoặc audit bảo mật toàn bộ dependency. Phạm vi gồm transport,
process lifecycle, backend, runtime, provider, HTTP host và CLI.

## Các lỗi phát hiện và đã sửa

| Mức độ | Vấn đề | Sửa và bằng chứng |
|---|---|---|
| P1 | Windows process có thể sinh con trước khi gắn Job Object; Python venv redirector đã tái hiện race, giữ cwd và làm cleanup lỗi | Spawn suspended → gắn Job Object → resume; kill và đợi active process bằng 0 trước xóa profile. `test_timeout_cancels_and_kills_descendant` đạt Windows/Linux |
| P1 | Đóng async generator ngoài chưa bảo đảm session bên trong được đóng ngay khi consumer dừng hoặc runtime queue đầy | Dùng `aclosing` ở cả runtime/backend; test đóng sau chunk đầu và runtime backpressure xác nhận cancel và PID đã chết trước khi trả lỗi |
| P2 | Shutdown có thể cạnh tranh với `submit`, cleanup của caller hoặc readiness còn đang chạy | Lock trạng thái runtime; release đợi shutdown đang diễn ra; backend theo dõi startup/request tasks. Tests shutdown giữa readiness và khi consumer đang chờ đều đạt |
| P2 | Validation chặn `max_retries=0` do LiteLLM tự chèn, khiến public LiteLLM API không gọi được provider | Cho phép đúng số nguyên 0, vẫn chặn retry khác; cả bốn đường public LiteLLM đạt |

Các kiểm tra hợp đồng bổ sung: từ chối slash commands, unsupported parameters,
alias sai, tool activity và permission; che upstream error text; giữ lỗi sau
text/SSE; không biến malformed/EOF/ID mismatch thành thành công; deadline bao
gồm queue wait. Terminal thành công chỉ được phát sau khi cleanup hoàn tất.

## Kết quả hiện tại

- Windows Python 3.13.12: **27/27 tests đạt**.
- Linux Docker Python 3.12: **27/27 tests đạt**, source read-only và network none.
- OpenAI SDK 3.49.0 qua localhost HTTP và agy 1.5.3 thật trên Windows:
  models, non-stream, SSE, usage, ngắt stream và alias sai đều đạt.
- Backend với agy 1.5.3 Linux thật, không credential/network: auth-required đạt;
  không gửi prompt và không thay đổi login hiện có.
- Ruff lint/format, lock/dependency check, build sdist/wheel đạt.

Không còn lỗi P1/P2 đã phát hiện trong bảng trên chưa sửa. Kết quả không chứng
minh không còn bug; xem phạm vi nghiệm thu và giới hạn dưới đây.

## Giới hạn cần giữ khi sử dụng

1. Chỉ bind localhost, một user text, CLI 1.5.3. Profile hạn chế không phải OS
   sandbox. CLI vẫn có quyền OS của tài khoản chạy; không dùng gateway này như
   môi trường thực thi code của người dùng không tin cậy. Linux cleanup theo
   process group không bao phủ process tự daemonize sang group khác.
2. Readiness xác minh session/config, không xác minh entitlement/quota. Live
   model chỉ thử `gemini-4-carbon`/`medium` trên Windows. Chưa nghiệm thu model
   authenticated trên Linux, tải lớn, chạy lâu hoặc restart/crash recovery.
3. `session/close` không xóa dữ liệu Antigravity. Gateway xóa cây tạm sau cleanup,
   nhưng chưa chứng minh retention phía dịch vụ hoặc thu hồi session sau crash.
4. API host gọi chung CustomLLM adapter trực tiếp; không chạy LiteLLM Proxy.
   Ứng dụng nhúng tự chịu cấu hình retry, callbacks/logging và token estimation
   của lớp LiteLLM bên ngoài. Host này không tự retry hoặc ước lượng token.
5. Workflow CI đã có; chưa push nên chưa có kết quả GitHub Actions, và chưa
   chạy Python 3.11 tại máy này. Dependency Pydantic phát warning về TypedDict
   ReadOnly trong LiteLLM; test vẫn đạt, chưa thay dependency để che warning.

Chi tiết lệnh/môi trường ở [VALIDATION](VALIDATION.md); hướng dẫn ở [README](../README.md).
