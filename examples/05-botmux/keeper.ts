// 进程脱离编排者常驻的最小骨架：CLI 跑在 detached tmux session 里，
// 编排进程只保存元数据；重启后按探测结果分三种处理，存活的分批重新接管。
// 对应第 5 章 4.2 节。依赖：系统装有 tmux。
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export interface SessionMeta {
  id: string;
  cliId: string;          // 起这个会话时用的 CLI
  cwd: string;
  status: 'active' | 'closed';
  suspended?: boolean;    // 被计数回收主动销毁过，下次冷恢复
}

type Probe = 'alive' | 'missing' | 'unknown';

// 从稳定 id 推出 session 名，不存映射表
export const sessName = (id: string) => `demo-${id.slice(0, 8)}`;

function tmux(args: string[]): { code: number | null; out: string } {
  const r = spawnSync('tmux', args, { encoding: 'utf-8', timeout: 3000 });
  if (r.error) return { code: null, out: '' };   // 超时或 tmux 不可用
  return { code: r.status, out: r.stdout };
}

// 探测单个 session：成功 / 明确不存在 / 说不准
export function probe(name: string): Probe {
  const r = tmux(['has-session', '-t', name]);
  if (r.code === 0) return 'alive';
  if (r.code === 1) return 'missing';
  return 'unknown';
}

// 整个 tmux server 是否在跑（宿主机重启后 server 不在，所有 pane 一起消失）
export function serverUp(): boolean {
  return tmux(['list-sessions']).code === 0;
}

// 新建：detached 启动，编排进程不持有任何 pty
export function start(meta: SessionMeta, bin: string, args: string[]): void {
  execFileSync('tmux', ['new-session', '-d', '-s', sessName(meta.id), '-c', meta.cwd, '--', bin, ...args]);
}

// 原子落盘，内容没变就不写
export function persist(file: string, all: Record<string, SessionMeta>): void {
  const next = JSON.stringify(all, null, 2);
  if (existsSync(file) && readFileSync(file, 'utf-8') === next) return;
  mkdirSync(dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  writeFileSync(tmp, next);
  renameSync(tmp, file);
}

export interface RestoreResult {
  reattach: string[];   // 存活，排队重新接管
  lazy: string[];       // 留着，等下一条消息冷恢复
  closed: string[];     // 真僵尸，关掉
  killed: string[];     // CLI 配置变了，杀掉旧 session
}

// 重启恢复：先同步分类，再分批接管
export function classify(all: Record<string, SessionMeta>, currentCliId: string): RestoreResult {
  const res: RestoreResult = { reattach: [], lazy: [], closed: [], killed: [] };
  let server: boolean | undefined;                    // 整个循环只查一次 server
  for (const m of Object.values(all)) {
    if (m.status !== 'active') continue;
    const name = sessName(m.id);
    const p = probe(name);
    if (p === 'missing') {
      if (m.suspended) { res.lazy.push(m.id); continue; }          // 预期中的缺席
      server ??= serverUp();
      if (!server) { res.lazy.push(m.id); continue; }              // 宿主机重启，别全关
      m.status = 'closed'; res.closed.push(m.id); continue;        // 只有这一个没了：僵尸
    }
    if (p === 'unknown') { res.lazy.push(m.id); continue; }        // 探测失败不下结论
    if (m.cliId !== currentCliId) {                                // 重连会复活旧 CLI
      tmux(['kill-session', '-t', name]); res.killed.push(m.id); continue;
    }
    res.reattach.push(m.id);
  }
  return res;
}

// 分批接管，避免几十个会话同时拉起 worker
export async function staggered(ids: string[], attach: (id: string) => void, batch = 5, delayMs = 250) {
  for (let i = 0; i < ids.length; i++) {
    attach(ids[i]);
    if ((i + 1) % batch === 0 && i + 1 < ids.length) await new Promise((r) => setTimeout(r, delayMs));
  }
}
