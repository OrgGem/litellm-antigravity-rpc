# LiteLLM compatibility spike

Kiểm tra ngày 2026-11-12. Chọn **LiteLLM 1.109.0** cho bước tích hợp tiếp theo,
đưa vào extra `spike`, chưa đưa vào runtime mặc định. Có wheel Windows/Linux
trên [PyPI 1.109.0](https://pypi.org/project/litellm/1.109.0/).

`scripts/spike_litellm.py` đăng ký một `CustomLLM` trả dữ liệu cố định trong
bộ nhớ, gọi qua API public của LiteLLM và kiểm tra nội dung, terminal stop.
Không có ACP backend, proxy HTTP hoặc model call trong spike này.

| Môi trường | sync | async | sync stream | async stream |
|---|---|---|---|---|
| Windows, Python 3.13.12 | Đạt | Đạt | Đạt | Đạt |
| Docker Linux, Python 3.12 | Đạt | Đạt | Đạt | Đạt |

OpenAI SDK được resolve: **3.49.0**. Response non-stream giữ `usage=None`.
Chưa kiểm `stream_options.include_usage`, token estimation, HTTP proxy, error
mapping, disconnect hoặc hiệu năng. Đây là pin tương thích ban đầu, không phải
chứng nhận production hay compatibility cho mọi phiên bản Python.

## Dependency lock

- `uv.lock`: lock project và optional dependencies, tạo bằng uv 0.12.17.
- `requirements-spike.lock`: export extra spike, pinned transitive versions và
  SHA-256, dùng `pip --require-hashes`/`uv pip --require-hashes`.
- Runtime mặc định vẫn chỉ dùng stdlib. Extra proxy và dependency của HTTP host
  sẽ được đánh giá tại Bước 3, không suy ra từ kết quả SDK này.

Tái hiện Windows:

```powershell
uv venv .local\litellm-spike --python .venv\Scripts\python.exe
uv pip sync --python .local\litellm-spike\Scripts\python.exe --require-hashes requirements-spike.lock
.\.local\litellm-spike\Scripts\python.exe scripts\spike_litellm.py
```

Tái hiện Linux (thay đường dẫn source bằng checkout của bạn):

```powershell
docker run --rm --mount 'type=bind,source=D:\Git\litellm-antigravity-rpc,target=/work,readonly' --workdir /work -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c 'python -m pip install --require-hashes -r requirements-spike.lock --quiet && python scripts/spike_litellm.py'
```

Image digest lúc kiểm tra:
`python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`.
Container test đã tự xóa; không publish port hoặc thay đổi container hiện có.
Việc tải package cần mạng; không tuyên bố spike hoàn toàn offline.

Nguồn: [LiteLLM CustomLLM](https://docs.litellm.ai/docs/providers/custom_llm_server)
và mã `litellm/llms/custom_llm.py`, `litellm/types/utils.py` trong wheel 1.109.0.
API được kiểm là `completion`, `acompletion`, `streaming`, `astreaming`;
streaming adapter trả `GenericStreamingChunk`.
