# Các bước triển khai

## Bước 0 Khởi tạo

- [x] Kiểm tra repository đích và công cụ Python/Git/agy.
- [x] Xác định chức năng và giới hạn MVP.
- [x] Tạo package, CLI doctor và backend contracts.
- [x] Hoàn tất môi trường ảo, lint, tests, build và handshake với CLI thật; ghi kết quả vào VALIDATION.md.

## Bước 1 Chốt hợp đồng ACP thực tế

- [x] Ghi metadata/capabilities từ initialize; không lưu credential.
- [x] Xác minh `session/new`, model config, model sai, text prompt, cancel và close với agy 1.5.3.
- [x] Tái hiện auth-required trên Linux/container sạch với đúng agy 1.5.3: -32000, không có credential hoặc model call.
- [x] Xác nhận profile workspace/gemini-dir riêng: agent tools rỗng; 6 hook/MCP canary ngoài profile không chạy. Positive control cho thấy chỉ dùng flags là **không đủ**: cả 4 hook/MCP trong cấu hình được chọn vẫn khởi động.
- [x] Kiểm tra close/load: close không xóa session; chưa có bằng chứng retention sau restart hoặc xóa dữ liệu.
- [x] Thu fixture live đã sanitize cho text chunk, lỗi model/auth, completion, usage, permission/deny và cancel; giữ ví dụ synthetic riêng với nhãn và nguồn.
- [x] Chốt usage giới hạn: input/output trên terminal result của đúng prompt; thử hai lượt cho output riêng, không cộng dồn. Cache details không ánh xạ khi chưa xác minh; thiếu counters để unknown.
- [x] Chọn LiteLLM 1.109.0; bốn đường gọi đạt Windows/Linux; tạo uv.lock và requirements-spike.lock có hash.

Đạt khi có protocol matrix, cấu hình hạn chế được kiểm chứng và fixture có nguồn rõ ràng.

**Kết luận kiểm chứng 2026-11-12: hoàn tất Bước 1 trong phạm vi matrix dưới đây.**
Xem [protocol matrix](PROTOCOL_MATRIX.md), [fixture](../tests/fixtures/acp/README.md)
và [LiteLLM spike](LITELLM_SPIKE.md). Đợt Bước 1 chỉ nghiên cứu; backend được
triển khai ở đợt tiếp theo theo yêu cầu "Implement chức năng và review code".

Điều kiện mang sang Bước 2: phải thực thi [profile riêng](RESTRICTED_PROFILE.md)
theo hướng fail-closed; không coi flags là sandbox, không tuyên bố close xóa
session, không ánh xạ cache/context counters chưa rõ. Việc stress process tree,
consumer chậm, race cancellation, cache usage nâng cao và E2E host còn thuộc
các bước sau. Model khác ngoài model đã thử không được coi là đã có entitlement.

## Bước 2 Backend

- [x] Transport/client bất đồng bộ, correlation, đọc song song notification và response.
- [x] Lifecycle một subprocess/session mỗi request, model selection, deadline và cancellation.
- [x] Từ chối permission, client file/terminal requests và nội dung ngoài phạm vi.
- [x] Tests: malformed frame, EOF, mismatched ID, lỗi sau text chunk, consumer chậm, cancel khi đợi/đang chạy và cleanup process tree.

Đạt khi backend trả text tăng dần, lỗi không bị mất và không để process/session chạy ngoài ý muốn.

## Bước 3 LiteLLM và API host

- [x] Provider sync/async và streaming/non-streaming; converters và strict validation.
- [x] Model aliases, `GET /v1/models`, `POST /v1/chat/completions`.
- [x] Lệnh `antigravity-rpc serve`, bind `127.0.0.1`, key riêng, readiness và graceful shutdown.
- [x] Kiểm thử OpenAI SDK với fake ACP agent trên Windows/Linux.
- [x] Thêm workflow CI Windows/Linux, Python 3.11/3.13; chưa chạy trên GitHub.

Đạt khi client tiêu chuẩn dùng được hai chế độ chat và không cần biết giao thức ACP.

## Bước 4 Nghiệm thu thật

- [x] Dùng login CLI hiện có trên Windows, không đọc/copy credential.
- [x] OpenAI SDK qua HTTP thật: text, SSE, usage, đóng giữa stream và alias sai.
- [x] Backend với CLI Linux 1.5.3 không credential/network trả auth-required, không gửi prompt.
- [x] Tests lọc thought, deny permission/tool và che upstream error; profile kế thừa canary Bước 1. Live smoke không gặp tool activity.
- [x] Ghi CLI/Python/LiteLLM/model, kết quả review và giới hạn trong VALIDATION.md/CODE_REVIEW.md.

Các lượt này gọi model và tiêu thụ quota; không nằm trong lệnh doctor.

**MVP Bước 2–4 hoàn tất trong phạm vi trên.** API host dùng ASGI mỏng và adapter
CustomLLM chung thay vì LiteLLM Proxy; xem quyết định trong ARCHITECTURE.md.
Chưa nghiệm thu Linux authenticated model E2E, load/soak, retention phía dịch vụ,
hoặc triển khai production. Đây không phải các chức năng đã được ngầm xác nhận.

## Sau MVP

Đánh giá session pooling, lịch sử opt-in và multimodal riêng theo nhu cầu. Không công bố tương thích toàn bộ OpenAI API hoặc RPC socket/WebSocket chỉ vì ACP dùng JSON-RPC.
