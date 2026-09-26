// 验证：只读连接能看到输出、写不进去；带 token 的连接能写。
import { startWebTerm } from './web-term.ts';

const listeners: ((s: string) => void)[] = [];
const received: string[] = [];
const source = {
  write(s: string) { received.push(s); listeners.forEach((l) => l(`echo:${s}`)); },
  onData(cb: (s: string) => void) { listeners.push(cb); },
};
const t = startWebTerm(source);

function connect(query: string): Promise<{ ws: WebSocket; seen: string[] }> {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://127.0.0.1:${t.port}/${query}`);
    const seen: string[] = [];
    ws.onmessage = (e) => seen.push(String(e.data));
    ws.onopen = () => resolve({ ws, seen });
  });
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const ro = await connect('');
const rw = await connect(`?token=${t.writeToken}`);
ro.ws.send('rm -rf /');          // 只读连接：应被丢弃
rw.ws.send('ls');                // 可写连接：应写进源
await sleep(100);
console.log('source received', received);
console.log('read-only saw', ro.seen, 'writer saw', rw.seen);
ro.ws.close(); rw.ws.close(); t.stop();
