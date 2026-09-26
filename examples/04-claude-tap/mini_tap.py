# mini_tap.py：双模式最小代理。reverse 靠改 base URL，forward 靠 CONNECT + MITM
# 依赖：pip install aiohttp cryptography；Python 3.11+（用到 StreamWriter.start_tls）
import asyncio, datetime, ipaddress, ssl, sys, tempfile
from pathlib import Path
from aiohttp import web, ClientSession
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# 这几个响应头要由本地重新决定：aiohttp 已经解压了 body，长度和分块方式也会变
DROP = {"connection", "keep-alive", "transfer-encoding", "content-encoding",
        "content-length", "upgrade"}

def clean(headers):
    return {k: v for k, v in headers.items() if k.lower() not in DROP}

# === 反向代理：child 把 base URL 指到这里 ===
async def run_reverse(upstream: str, port: int):
    session = ClientSession()

    async def handler(request: web.Request):
        body = await request.read()
        hdrs = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        hdrs["Accept-Encoding"] = "identity"
        async with session.request(request.method, upstream + request.path_qs,
                                   headers=hdrs, data=body) as resp:
            data = await resp.read()
        print(f"[reverse] {request.method} {request.path} -> {resp.status} {len(data)}B")
        return web.Response(status=resp.status, body=data, headers=clean(resp.headers))

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"ANTHROPIC_BASE_URL=http://127.0.0.1:{port}")
    await asyncio.Event().wait()

# === 正向代理 + MITM：child 设 HTTPS_PROXY 并信任我们的 CA ===
def _cert(subject, issuer, pubkey, sign_key, days, *exts):
    now = datetime.datetime.now(datetime.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
         .public_key(pubkey).serial_number(x509.random_serial_number())
         .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=days)))
    for ext, critical in exts:
        b = b.add_extension(ext, critical=critical)
    return b.sign(sign_key, hashes.SHA256())

def make_ca():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mini-tap CA")])
    cert = _cert(name, name, key.public_key(), key, 365,
                 (x509.BasicConstraints(ca=True, path_length=0), True),
                 (x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                content_commitment=False, key_encipherment=False,
                                data_encipherment=False, key_agreement=False,
                                encipher_only=False, decipher_only=False), True),
                 (x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False))
    return cert, key

_ctx_cache: dict[str, ssl.SSLContext] = {}

def host_ctx(host: str, ca_cert, ca_key) -> ssl.SSLContext:
    if host in _ctx_cache:                     # 每个域名只签一次
        return _ctx_cache[host]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try:
        san = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    cert = _cert(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]),
                 ca_cert.subject, key.public_key(), ca_key, 365,
                 (x509.SubjectAlternativeName([san]), False),   # 客户端只认 SAN
                 (x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False))
    d = Path(tempfile.mkdtemp())
    (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "cert.pem", d / "key.pem")
    _ctx_cache[host] = ctx
    return ctx

async def read_head(reader):
    lines = []
    while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
        lines.append(line.decode().rstrip("\r\n"))
    return lines

async def handle(reader, writer, ca, session):
    head = await read_head(reader)
    if not head or not head[0].startswith("CONNECT "):
        writer.close()                         # 明文 HTTP 代理从略
        return
    host, port = head[0].split(" ")[1].rsplit(":", 1)
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await writer.start_tls(host_ctx(host, *ca))   # 从这里起 reader 读到的是明文
    while head := await read_head(reader):         # 一条隧道里可以有多个请求
        method, path, _ = head[0].split(" ", 2)
        hdrs = dict(h.split(": ", 1) for h in head[1:])
        size = int({k.lower(): v for k, v in hdrs.items()}.get("content-length", 0))
        body = await reader.readexactly(size)      # 示意：不处理 chunked 请求体
        hdrs = {k: v for k, v in hdrs.items() if k.lower() != "accept-encoding"}
        hdrs["Accept-Encoding"] = "identity"
        async with session.request(method, f"https://{host}:{port}{path}",
                                   headers=hdrs, data=body) as resp:
            data = await resp.read()               # 整包回传；流式写法见正文 4.2 节
        print(f"[forward] {method} {host}{path} -> {resp.status} {len(data)}B")
        out = f"HTTP/1.1 {resp.status} {resp.reason}\r\n"
        out += "".join(f"{k}: {v}\r\n" for k, v in clean(resp.headers).items())
        writer.write(out.encode() + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        await writer.drain()
    writer.close()

async def run_forward(port: int):
    ca = make_ca()                             # 真实工具要把 CA 持久化，否则每次启动都要重新信任
    Path("mini-tap-ca.pem").write_bytes(ca[0].public_bytes(serialization.Encoding.PEM))
    session = ClientSession()
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, ca, session), "127.0.0.1", port)
    print(f"HTTPS_PROXY=http://127.0.0.1:{port} NODE_EXTRA_CA_CERTS=mini-tap-ca.pem")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    # 用法：python mini_tap.py reverse [上游URL] | python mini_tap.py forward；端口用 PORT 环境变量改，默认 8080
    import os
    port = int(os.environ.get("PORT", "8080"))
    if len(sys.argv) > 1 and sys.argv[1] == "reverse":
        asyncio.run(run_reverse(sys.argv[2] if len(sys.argv) > 2 else "https://api.anthropic.com", port))
    else:
        asyncio.run(run_forward(port))
