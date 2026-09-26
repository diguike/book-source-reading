# 第 3 章配套：刷新锁误删复现

`lock_race.py` 在真实 Redis 上跑两遍同一个时序：锁 TTL 300ms，副本 A 的刷新要 500ms。

- `naive`：锁的值固定为 1，释放时无条件 `DEL`（sub2api `internal/repository/gemini_token_cache.go:41-49` 的写法）
- `owner`：锁的值是持有者 token，释放时用 Lua 比较后再删

## 运行

```bash
pip install -r requirements.txt
redis-server --port 6399 --save '' --daemonize yes
python lock_race.py 6399
redis-cli -p 6399 shutdown nosave
```

Redis 6 以下的服务端不支持 RESP3，脚本里已用 `protocol=2` 连接。
