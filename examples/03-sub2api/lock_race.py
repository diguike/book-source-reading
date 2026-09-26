# 复现：锁 TTL 短于刷新耗时时，无 owner 的释放会删掉别人的锁
import sys, threading, time, uuid
import redis

r = redis.Redis(port=int(sys.argv[1]), protocol=2)
KEY = "refresh:acct-1"
TTL_MS = 300          # 锁 TTL
A_WORK_S = 0.5        # A 的刷新耗时，超过 TTL
UNLOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""
t0 = time.monotonic()
def log(msg): print(f"[{(time.monotonic()-t0)*1000:4.0f}ms] {msg}")

def acquire(mode, owner):
    val = 1 if mode == "naive" else owner
    return r.set(KEY, val, nx=True, px=TTL_MS)

def release(mode, owner):
    if mode == "naive":
        r.delete(KEY)                    # sub2api 的写法：无条件 Del
    else:
        r.eval(UNLOCK, 1, KEY, owner)    # 只删自己持有的锁

def worker(name, mode, start, work):
    time.sleep(start)
    owner = f"{name}-{uuid.uuid4().hex[:6]}"
    if not acquire(mode, owner):
        log(f"{name} 抢锁失败，锁被别人持有")
        return
    log(f"{name} 拿到锁，开始刷新")
    time.sleep(work)
    release(mode, owner)
    log(f"{name} 刷完，释放锁；此刻锁的值 = {r.get(KEY)}")

for mode in ("naive", "owner"):
    r.delete(KEY); t0 = time.monotonic()
    print(f"--- {mode} ---")
    ts = [threading.Thread(target=worker, args=a) for a in
          [("A", mode, 0.0, A_WORK_S), ("B", mode, 0.35, 0.4), ("C", mode, 0.55, 0.1)]]
    [t.start() for t in ts]; [t.join() for t in ts]
