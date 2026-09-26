# 第 5 章配套示例：botmux 的三个做法

三组文件各对应正文一节，互相独立，用 Bun 运行，都过了 `tsc --strict`。

```bash
bun install          # 只装类型检查用的 typescript 和 @types/bun
bun run test         # 跑下面三个测试
bun run typecheck
```

## keeper.ts：CLI 常驻 tmux，重启后分类接管（4.2 节）

- CLI 用 `tmux new-session -d` 跑在 detached session 里，编排进程不持有 pty
- session 名从会话 id 推出，元数据原子落盘，内容没变就不写
- 重启时逐个探测：存活的分批重新接管（5 个一批，间隔 250ms）；被计数回收过的、tmux server 整体不在的、探测失败的都留着等下一条消息；server 在而这一个 session 没了的才当僵尸关掉；CLI 配置变了的杀掉旧 session

`keeper.test.ts` 需要系统装有 tmux。它用 `TMUX_TMPDIR` 起一个独立的 tmux server，用 `sleep` 代替真 CLI，最后会 `kill-server`，不碰你自己的 tmux 会话。

## web-term.ts：一个源，读写按 token 分开（4.3 节）

- 同一个数据源广播给所有 WebSocket 连接
- 握手时带对了写 token 的连接才能写回，只读连接的输入直接丢弃

真实实现里数据源是 PTY，测试里用一个回显的假源。

## gates.ts：对话闸和操作闸（4.4 节）

- `canTalk` 认 owner、兄弟 bot、授权群和对话授权名单
- `canOperate` 放行只认 owner 和兄弟 bot；对话授权名单只用来判断是不是 open 模式

`gates.test.ts` 覆盖了只配对话授权时不能退化成全开放的情况。
