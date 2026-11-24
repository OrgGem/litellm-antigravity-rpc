# LiteLLM Antigravity RPC

Gateway tương thích OpenAI (`Chat Completions` + `Responses API`) và LiteLLM
`CustomLLM` provider cho Antigravity CLI (`agy acp`) qua JSON-RPC stdio.

## Chạy API host cục bộ

Yêu cầu Python 3.11+, `uv`, và **agy 1.5.x** (từ 1.5.3 trở lên) đã đăng nhập bằng
luồng chính thức:

```powershell
uv sync --extra dev --frozen
uv run antigravity-rpc doctor
uv run antigravity-rpc serve --port 4000
```

Nếu chưa đặt `ANTIGRAVITY_RPC_API_KEY` hoặc `LITELLM_MASTER_KEY`, host sẽ tự sinh
session key cho phiên chạy. Có thể truyền `--models models.example.json`, `--model`,
`--model-name`, `--thought-level`, `--agy-bin`, `--timeout`, `--concurrency` và
`--allow-prompt-flattening`.

Cũng có thể chạy trực tiếp với LiteLLM Proxy qua `config.example.yaml` +
`proxy_handler.py`:

```powershell
$env:LITELLM_MASTER_KEY = "sk-your-proxy-key-at-least-16"
litellm --config config.example.yaml --port 4000
```

## Gọi bằng OpenAI SDK

Hỗ trợ `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/responses` (cả
normal JSON lẫn `stream=True` SSE) và `GET /health/ready`:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:4000/v1",
    api_key=os.environ["ANTIGRAVITY_RPC_API_KEY"],
    max_retries=0,
)
chat = client.chat.completions.create(
    model="antigravity",
    messages=[{"role": "user", "content": "Giải thích ngắn gọn ACP là gì."}],
)
print(chat.choices[0].message.content)

resp = client.responses.create(
    model="antigravity",
    instructions="Trả lời ngắn gọn.",
    input="Viết một lời chào ngắn.",
    store=False,
)
print(resp.output_text)
```

## Cấu hình qua biến môi trường

| Biến | Mặc định |
| --- | --- |
| `ANTIGRAVITY_MODEL_MAP` | `{"antigravity":{"model":"gemini-4-carbon","thought_level":"medium"}}` |
| `ANTIGRAVITY_RPC_EXECUTABLE` | `agy` |
| `ANTIGRAVITY_RPC_REQUEST_TIMEOUT` | `120` |
| `ANTIGRAVITY_RPC_CLEANUP_TIMEOUT` | `3` |
| `ANTIGRAVITY_RPC_MAX_CONCURRENT` | `2` |
| `ANTIGRAVITY_RPC_MAX_PENDING` | `16` |
| `ANTIGRAVITY_RPC_ALLOW_PROMPT_FLATTENING` | `false` |
| `ANTIGRAVITY_RPC_LIMITS` | `{}` |
| `ANTIGRAVITY_RPC_API_KEY` / `LITELLM_MASTER_KEY` | tự sinh khi `serve` |

## Hợp đồng và giới hạn

- Mặc định nhận một user message (string hoặc text blocks). Khi bật
  `--allow-prompt-flattening` / `ANTIGRAVITY_RPC_ALLOW_PROMPT_FLATTENING=true`,
  chấp nhận lịch sử nhiều lượt cùng role `system`, `developer`, `assistant`,
  `user` dưới dạng JSON có thứ tự (không cam kết ngữ nghĩa role gốc).
- `/v1/responses` chạy stateless (`store: false`), hỗ trợ `instructions` cấp cao
  nhất, `input` dạng chuỗi hoặc message items (`input_text` / `output_text`,
  assistant `phase`), và từ chối `store: true`, `previous_response_id`, tools,
  multimodal, sampling params.
- Usage ánh xạ `inputTokens`, `outputTokens`, `totalTokens` cùng chi tiết
  `cached_tokens`, `cache_write_tokens`, `reasoning_tokens` (`thoughtTokens`) khi
  ACP cung cấp; thiếu counter giữ `None`/bỏ trường, không tự ước tính.
- V2 native/answer-only driver vẫn giữ nguyên gate đóng cho tới khi có receipt
  nghiệm thu phiên bản tương ứng.
