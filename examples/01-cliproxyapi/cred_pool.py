"""最小凭证池：统一执行接口、按模型冷却、401 自动续期，调度策略可选 round-robin / fill-first。

对应第 1 章 4.2 节。运行：python3 cred_pool.py
"""
import itertools
import time
from dataclasses import dataclass, field
from typing import Callable


class QuotaError(Exception):
    """后端返回 429，带上还要多久恢复。"""

    def __init__(self, reset_in: float):
        super().__init__(f"quota exceeded, reset in {reset_in}s")
        self.reset_in = reset_in


class AuthExpired(Exception):
    """后端返回 401。"""


@dataclass
class Cred:
    name: str
    refresh: Callable[[], None]  # 续期函数由凭证自己提供，对应 ProviderExecutor.Refresh
    cooldown_until: dict[str, float] = field(default_factory=dict)  # model -> 解冻时间
    disabled: bool = False  # 操作员意图，和临时冷却分开记

    def usable(self, model: str, now: float) -> bool:
        return not self.disabled and self.cooldown_until.get(model, 0) <= now


class CredPool:
    def __init__(self, creds: list[Cred], strategy: str = "round-robin"):
        self.creds = creds  # 顺序固定，是 fill-first 的基础
        self.strategy = strategy
        self._rr = itertools.count()  # round-robin 游标

    def pick(self, model: str) -> Cred:
        now = time.time()
        ready = [c for c in self.creds if c.usable(model, now)]
        if not ready:
            raise RuntimeError(f"all creds cooling down for {model}")
        if self.strategy == "fill-first":
            return ready[0]  # 前面的用到触顶才轮到后面
        return ready[next(self._rr) % len(ready)]  # 默认：轮流摊匀

    def execute(self, model: str, call: Callable[[Cred], str]) -> str:
        tried: set[str] = set()
        while True:
            c = self.pick(model)
            if c.name in tried:
                raise RuntimeError(f"no more creds for {model}")
            tried.add(c.name)
            try:
                return call(c)
            except QuotaError as e:
                c.cooldown_until[model] = time.time() + e.reset_in  # 只冷却这个模型
            except AuthExpired:
                c.refresh()
                return call(c)  # 续期后重试一次


if __name__ == "__main__":
    refreshed = []
    a = Cred("a", refresh=lambda: refreshed.append("a"))
    b = Cred("b", refresh=lambda: refreshed.append("b"))

    # fill-first：a 在 sonnet 上配额触顶后换 b，haiku 仍走 a
    pool = CredPool([a, b], strategy="fill-first")

    def call_sonnet(c: Cred) -> str:
        if c.name == "a":
            raise QuotaError(reset_in=60)
        return c.name

    assert pool.execute("sonnet", call_sonnet) == "b"
    assert pool.execute("haiku", lambda c: c.name) == "a"

    # 401：续期后重试成功
    state = {"expired": True}

    def call_401(c: Cred) -> str:
        if state["expired"]:
            state["expired"] = False
            raise AuthExpired()
        return c.name

    assert pool.execute("haiku", call_401) == "a" and refreshed == ["a"]

    # round-robin：两个凭证轮流
    rr = CredPool([Cred("x", refresh=lambda: None), Cred("y", refresh=lambda: None)])
    assert [rr.execute("m", lambda c: c.name) for _ in range(4)] == ["x", "y", "x", "y"]

    # 全部冷却时报错
    try:
        pool.execute("sonnet", lambda c: (_ for _ in ()).throw(QuotaError(60)))
    except RuntimeError as e:
        print("expected:", e)
    print("ok")
