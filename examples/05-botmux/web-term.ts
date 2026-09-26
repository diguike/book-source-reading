// Web 终端读写分权的最小骨架：同一个数据源扇出给所有连接，
// 只有握手时带对了写 token 的连接能写回。对应第 5 章 4.3 节。
// 真实实现里 source 是 PTY；这里用一个回显的假源代替，方便测试。
import { randomBytes } from 'node:crypto';

export function startWebTerm(source: { write(s: string): void; onData(cb: (s: string) => void): void }) {
  const writeToken = randomBytes(16).toString('hex');   // 每个会话随机生成，经私聊下发
  const server = Bun.serve<{ writable: boolean }>({
    port: 0,
    fetch(req, srv) {
      const token = new URL(req.url).searchParams.get('token');
      const writable = token !== null && token === writeToken;
      return srv.upgrade(req, { data: { writable } }) ? undefined : new Response('ws only', { status: 400 });
    },
    websocket: {
      open(ws) { ws.subscribe('out'); },                 // 所有连接都能看
      message(ws, raw) {
        if (!ws.data.writable) return;                   // 只读连接的输入直接丢弃
        source.write(String(raw));
      },
    },
  });
  source.onData((s) => server.publish('out', s));        // 一个源，广播给所有连接
  return { port: server.port, writeToken, stop: () => server.stop(true) };
}
