"""最小热重载：防抖、内容哈希、原子替换检测、解析失败保留旧配置。

对应第 1 章 4.3 节。运行：pip install -r requirements.txt && python3 hot_reload.py
"""
import hashlib
import json
import os
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class ReloadHandler(FileSystemEventHandler):
    def __init__(self, path, on_reload, on_remove, debounce=0.15, replace_delay=0.05):
        self.path = os.path.abspath(path)
        self.on_reload, self.on_remove = on_reload, on_remove
        self.debounce, self.replace_delay = debounce, replace_delay
        self.last_hash = None
        self.timer = None
        self.lock = threading.Lock()

    def on_any_event(self, event):
        paths = {os.path.abspath(event.src_path)}
        if getattr(event, "dest_path", ""):
            paths.add(os.path.abspath(event.dest_path))
        if self.path not in paths:
            return
        with self.lock:
            if self.timer:
                self.timer.cancel()  # 防抖：只保留最后一次
            self.timer = threading.Timer(self.debounce, self._settle)
            self.timer.start()

    def _settle(self):
        if not os.path.exists(self.path):
            time.sleep(self.replace_delay)  # 原子替换：等新文件就位
            if not os.path.exists(self.path):
                self.on_remove()  # 真删除
                return
        try:
            data = open(self.path, "rb").read()
        except FileNotFoundError:
            return
        if not data:
            return  # 替换中途读到空文件，等下一次事件
        h = hashlib.sha256(data).hexdigest()
        if h == self.last_hash:
            return  # 内容没变
        if self.on_reload(data):  # 解析成功才记住哈希
            self.last_hash = h


class Config:
    def __init__(self):
        self.current = {}
        self.lock = threading.Lock()

    def load(self, data: bytes) -> bool:
        try:
            new = json.loads(data)  # 先完整解析
        except ValueError:
            return False  # 解析失败，旧配置继续生效
        with self.lock:
            self.current = new  # 构造好之后一次替换
        return True


if __name__ == "__main__":
    import tempfile

    d = tempfile.mkdtemp()
    path = os.path.join(d, "config.json")
    open(path, "w").write('{"v": 0}')
    cfg = Config()
    reloads, removes = [], []

    def on_reload(data):
        ok = cfg.load(data)
        if ok:
            reloads.append(cfg.current["v"])
        return ok

    h = ReloadHandler(path, on_reload, lambda: removes.append(1))
    obs = Observer()
    obs.schedule(h, d)
    obs.start()
    try:
        for i in (1, 2, 3):  # 连写三次，只重载一次
            open(path, "w").write(json.dumps({"v": i}))
        time.sleep(0.5)
        open(path, "w").write('{"v": 3}')  # 内容没变
        time.sleep(0.5)
        open(path, "w").write("{not json")  # 解析失败
        time.sleep(0.5)
        tmp = path + ".tmp"  # 原子替换
        open(tmp, "w").write('{"v": 4}')
        os.replace(tmp, path)
        time.sleep(0.5)
        os.remove(path)  # 真删除
        time.sleep(0.5)
    finally:
        obs.stop()
        obs.join()
    print("reloads", reloads, "config", cfg.current, "removes", len(removes))
    assert reloads == [3, 4] and cfg.current == {"v": 4} and removes == [1]
    print("ok")
