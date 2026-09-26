# 第 4 章配套示例：claude-tap 的三个模式

`mini_tap.py`、`mini_sse.py`、`mini_strategy_table.py` 各对应正文 4.1、4.2、4.3 节；`mini_recorder.py` 对应第 7 节的前两步，用到 `mini_sse.py`。都在 Python 3.11 下跑过。

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## mini_tap.py：双模式最小代理

反向模式（child 能改 base URL 时用）：

```bash
python mini_tap.py reverse https://api.anthropic.com
# 另一个终端
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude
```

正向模式（child 改不了 base URL，但认 `HTTPS_PROXY` 时用）：

```bash
python mini_tap.py forward          # 启动时在当前目录写出 mini-tap-ca.pem
# 另一个终端
HTTPS_PROXY=http://127.0.0.1:8080 NODE_EXTRA_CA_CERTS=$PWD/mini-tap-ca.pem claude
# 或者先用 curl 验证 CA 信任和隧道
curl --proxy http://127.0.0.1:8080 --cacert mini-tap-ca.pem https://api.anthropic.com/v1/models
```

端口用 `PORT` 环境变量改。为了短，它省掉了明文 HTTP 代理、chunked 请求体和流式响应（整包回传），每次启动都会新生成一张 CA。

## mini_sse.py：最小 SSE 累积器

```bash
python mini_sse.py
```

自带一段 Anthropic 流式响应样本，按 7 字节一片喂进去，打印累积出的完整消息。只处理文本块和 `message_delta`，工具参数和 OpenAI 协议留给读者补。

## mini_strategy_table.py：多 client 策略表

```bash
python mini_strategy_table.py
```

只打印解析出的环境变量，不真正启动 CLI。要真的拉起 child，在自己的代码里调 `run("claude", port, args)`。

## mini_recorder.py：包一层就能录的记录器

```bash
python mini_recorder.py --out trace.jsonl -- claude
# 上游默认 https://api.anthropic.com，用 --target 换
```

在系统挑的空闲端口起反向代理，把 `ANTHROPIC_BASE_URL` 指向自己再拉起 child。每个请求写一行 JSONL：请求头（`authorization` / `x-api-key` 只留前 12 个字符）、请求体、状态码、耗时，以及响应。流式响应边转发边用 `mini_sse` 累积，存下的是完整消息。child 退出后记录器随之退出。
