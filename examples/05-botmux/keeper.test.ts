// 用真实 tmux 验证 keeper.ts：新建、分类、分批接管。
// 用独立的 tmux socket（-L），不碰你自己的 tmux 会话。
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { classify, persist, sessName, staggered, start, type SessionMeta } from './keeper.ts';
import { execFileSync } from 'node:child_process';

process.env.TMUX_TMPDIR = mkdtempSync(join(tmpdir(), 'keeper-'));   // 独立 server
const cwd = process.cwd();
const all: Record<string, SessionMeta> = {};
for (let i = 0; i < 7; i++) {
  const id = `sess${i}xxxxxxxx`;
  all[id] = { id, cliId: 'claude-code', cwd, status: 'active' };
  start(all[id], 'sleep', ['600']);                  // 用 sleep 代替真 CLI
}
all.sess5xxxxxxxx.cliId = 'codex';                   // 模拟 bot 换了 CLI
execFileSync('tmux', ['kill-session', '-t', sessName('sess6xxxxxxxx')]);   // 一个僵尸
all.sess7xxxxxxxx = { id: 'sess7xxxxxxxx', cliId: 'claude-code', cwd, status: 'active', suspended: true };

const file = join(process.env.TMUX_TMPDIR, 'sessions.json');
persist(file, all);

const r = classify(all, 'claude-code');
console.log('reattach', r.reattach.length, 'lazy', r.lazy, 'closed', r.closed, 'killed', r.killed);

const t0 = Date.now();
const stamps: number[] = [];
await staggered([...r.reattach, 'x1', 'x2', 'x3', 'x4', 'x5', 'x6'], () => stamps.push(Date.now() - t0));   // 凑够 11 个看分批
console.log('attach times(ms)', stamps.map((s) => Math.round(s / 10) * 10));

execFileSync('tmux', ['kill-server']);
// server 整体不在时，missing 的会话不关
const afterReboot = classify(
  { a: { id: 'sess0xxxxxxxx', cliId: 'claude-code', cwd, status: 'active' } }, 'claude-code');
console.log('after server down: lazy', afterReboot.lazy, 'closed', afterReboot.closed);
