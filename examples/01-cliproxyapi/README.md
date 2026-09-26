# 第 1 章配套代码：CLIProxyAPI

| 文件 | 对应小节 | 内容 |
|---|---|---|
| `cred_pool.py` | 4.2 | 统一执行接口、按模型冷却、401 续期、round-robin / fill-first |
| `hot_reload.py` | 4.3 | 防抖、内容哈希、原子替换检测、解析失败保留旧配置 |

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 cred_pool.py     # 输出 ok
python3 hot_reload.py    # 输出 reloads [3, 4] ... ok
```

需要 Python 3.10 以上。
