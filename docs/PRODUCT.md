# Phạm vi chức năng

## Mục tiêu

Cho phép ứng dụng hỗ trợ OpenAI Chat Completions gọi model thông qua phiên Antigravity CLI. API key của gateway bảo vệ gateway; credential Antigravity thuộc CLI và có vòng đời riêng.

## MVP đã triển khai

| Chức năng | Hành vi |
|---|---|
| Chat | Nhận một user message text, trả `choices[0].message.content` |
| Streaming | Chuyển text chunk của agent thành SSE; kết thúc theo kết quả prompt |
| Model | Alias cấu hình ánh xạ model có thật; từ chối alias/model không hợp lệ |
| Session | Tạo mới mỗi request; không dùng lại lịch sử giữa người gọi |
| Xác thực | CLI dùng đăng nhập chính thức; gateway dùng Bearer key riêng |
| Timeout | Deadline gồm đợi semaphore, khởi động, bắt tay và prompt |
| Hủy | Gửi `session/cancel`, đợi có giới hạn, dọn process khi cần |
| Lỗi | Phân biệt authentication, quota, model, protocol, timeout và cancellation |
| Usage | Chỉ trả token count có bằng chứng cho request; thiếu dữ liệu thì để unknown |
| Quan sát | Request ID, model alias, thời lượng, thời gian tới chunk đầu và trạng thái |

Không ghi prompt, câu trả lời, token đăng nhập hoặc stderr thô vào log mặc định. Không tự động chạy lại prompt sau mất kết nối vì không biết lượt trước đã hoàn tất hay chưa.

## Chưa nằm trong MVP

OpenAI tool calling, ảnh/audio, Responses API, embeddings, JSON schema, sampling/token limits, session dùng lại và dịch vụ ACP từ xa. Lịch sử nhiều role có thể bổ sung bằng chế độ flattening opt-in; không tuyên bố bảo toàn chính xác system/developer role.

## Điều kiện chấp nhận

1. Client OpenAI gọi được non-stream và nhận SSE tăng dần với cùng nội dung.
2. Hai request không chia sẻ context; client ngắt kết nối không để lại lượt chạy vô hạn.
3. Lỗi sau khi stream đã bắt đầu không bị biến thành kết thúc thành công.
4. Chỉ chấp nhận chức năng hỗ trợ; không bỏ qua tham số người dùng một cách im lặng.
5. Không có tool ngoài ý muốn trong cấu hình đã kiểm chứng. Khả năng tắt tool nội bộ phải được xác minh trước khi công bố provider chỉ sinh text.
6. Kiểm thử fixture trên Windows/Linux và một lượt model thật có xác thực đều đạt; handshake đơn thuần chưa đủ.

Stateless ở API không đồng nghĩa Antigravity không lưu hội thoại trên đĩa. Cần xác minh retention và cơ chế đóng/xóa session trước khi công bố đặc tính ephemeral.
