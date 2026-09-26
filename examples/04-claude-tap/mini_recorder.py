# mini_recorder.py：最小的"包一层就能录"的流量记录器
# 反向代理挑一个空闲端口 → 把 ANTHROPIC_BASE_URL 指向自己再拉起 child →
# 每个请求写一行 JSONL，流式响应边转发边用 mini_sse 累积成完整消息
# 用法：python mini_recorder.py [--target URL] [--out trace.jsonl] -- claude [参数...]
import asyncio, json, os, sys, time
from aiohttp import web, ClientSession
from mini_sse import MiniSSEReassembler

HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding",
       "content-length", "upgrade", "host"}
SECRET = {"authorization", "x-api-key"}

def redact(headers):
    return {k: (v[:12] + "..." if k.lower() in SECRET else v) for k, v in headers.items()}

async def main(target, out_path, child_argv):
    session = ClientSession()
    out = open(out_path, "a", encoding="utf-8")
    turn = 0

    async def handler(request: web.Request):
        nonlocal turn
        turn += 1
        started = time.monotonic()
        body = await request.read()
        hdrs = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        hdrs["Accept-Encoding"] = "identity"            # 不让上游压缩，方便解析
        record = {"turn": turn, "method": request.method, "path": request.path_qs,
                  "request_headers": redact(request.headers),
                  "request_body": json.loads(body) if body else None}
        async with session.request(request.method, target + request.path_qs,
                                   headers=hdrs, data=body) as up:
            headers = {k: v for k, v in up.headers.items() if k.lower() not in HOP}
            if "text/event-stream" in up.headers.get("Content-Type", ""):
                resp = web.StreamResponse(status=up.status, headers=headers)
                await resp.prepare(request)
                sse = MiniSSEReassembler()
                async for chunk in up.content.iter_any():
                    await resp.write(chunk)              # 先转发
                    sse.feed_bytes(chunk)                # 再累积
                await resp.write_eof()
                record["response"] = sse.snapshot
            else:
                data = await up.read()
                resp = web.Response(status=up.status, body=data, headers=headers)
                record["response"] = data.decode("utf-8", "replace")
        record.update(status=up.status, duration_ms=int((time.monotonic() - started) * 1000))
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        print(f"[turn {turn}] {request.method} {request.path} -> {up.status}", file=sys.stderr)
        return resp

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)           # 端口 0：让系统挑一个空闲端口
    await site.start()
    port = runner.addresses[0][1]
    env = {**os.environ, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}"}
    env.pop("CLAUDECODE", None)                          # 避免被识别成嵌套调用
    child = await asyncio.create_subprocess_exec(*child_argv, env=env)
    code = await child.wait()
    await runner.cleanup()
    await session.close()
    out.close()
    print(f"child exited {code}, {turn} turns -> {out_path}", file=sys.stderr)
    return code

if __name__ == "__main__":
    args = sys.argv[1:]
    target, out_path = "https://api.anthropic.com", "trace.jsonl"
    while args and args[0] != "--":
        flag, value, args = args[0], args[1], args[2:]
        if flag == "--target":
            target = value.rstrip("/")
        elif flag == "--out":
            out_path = value
    child_argv = args[1:] if args else ["claude"]
    sys.exit(asyncio.run(main(target, out_path, child_argv)))
