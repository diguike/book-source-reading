---
title: 第 3 章　sub2api：把有状态的订阅后端做成多账号 API 网关
project_url: https://github.com/Wei-Shaw/sub2api
based_on_tag: v0.1.133
based_on_commit: 68901cb
written_at: 2026-06-02
learning_objectives:
  - 能给有状态的上游做粘性会话调度：按会话标识三层递进地路由（先按上游返回的 id 粘、再按请求内容 hash 粘、最后才负载均衡），让续聊请求落回同一后端账号
  - 能写一个 HTTP 入、WebSocket 出的协议桥接 forwarder，把客户端的 HTTP/SSE 和上游的 WebSocket 桥起来，并在首字前缓冲非 token 事件，让上游早断时还能重试或干净地报错
  - 能给 OAuth token 池做并发安全的刷新：进程内锁 + 分布式锁 + 加锁后 DB 重读 + invalid_grant 竞争恢复，避免多副本重复消费同一 refresh token
feishu_url: "https://fivwvysqdz.feishu.cn/wiki/CYwewUxRUiCf5jkQcHAcA7FhnTb"
last_synced: "2026-08-02"
---

## 1. sub2api

[sub2api](https://github.com/Wei-Shaw/sub2api) 是一个把订阅版 LLM（Claude Pro、ChatGPT Pro、Gemini 等）的能力做成多人共用 API 网关的开源 SaaS。它有完整的用户体系、API Key 发放、多账号池调度、计费、支付集成。后端 Go + Ent ORM + PostgreSQL + Redis，前端 Vue3，是一套能直接部署上线的系统。

### 1.1 痛点：订阅是"个人坐席"，但你想给一群人用

最新最强的模型几乎都先以订阅形式发布。一个 200 美元的 Pro 订阅，一个人用不完，团队几个人合用、或者给脚本和评测台共享，是很自然的需求。但订阅卖的是个人坐席，没有 API key。

把单个 CLI 的 OAuth 翻成 API 的本地代理（如 CLIProxyAPI 这类），解决的是"我一个人本地用"。要把订阅资源做成给一群人用的服务，要处理的事情完全是另一个量级：

- 多个订阅账号组成一个池，请求来了选哪个账号、某个账号 429 了怎么冷却切换
- 上游是**有状态的**：OpenAI 的 Responses API 续聊要带 `previous_response_id`，这个 id 只在当初生成它的那个账号上有效，跨账号就失效，不能随便负载均衡
- OpenAI 的 Responses API 除了 HTTP/SSE 还有 WebSocket 模式，长连接能复用、省掉每次握手，但客户端大多只会发 HTTP 请求
- 多副本部署时，同一个账号的 OAuth token 过期，几个副本可能同时去刷新，把上游打出 `invalid_grant`（OAuth 标准错误码，意思是这个 refresh_token 已被消费或失效）

单进程、单用户的本地代理遇不到这些问题。sub2api 对每一个都有具体的处理，其中不少做法可以搬到别的系统。

### 1.2 三个可迁移工程模式

下面三个工程手法和"把订阅当 API 卖"这件业务无关，可以迁移到很多系统：

1. **有状态后端的粘性会话调度（session affinity）**。上游一旦有状态（续聊要回到同一账号、购物车绑同一 server、有 server-side session），就不能纯负载均衡。sub2api 用三层递进的粘性兜底解决。会话要粘到固定后端的路由都用得上。

2. **HTTP 入、WebSocket 出的协议桥接**。客户端讲 HTTP/SSE，上游走 WebSocket，中间要做一座桥，还要在首字前缓冲事件，让上游早断时还有机会重试。两端协议形态不同的代理都会遇到这个问题。

3. **OAuth token 池的并发安全刷新**。多副本下同一个 refresh token 不能被重复消费。sub2api 用进程内锁、分布式锁、加锁后重读 DB、invalid_grant 竞争恢复四道防御。多副本共享一份会过期凭证的系统都需要。

本地对照源码看：

```bash
git clone --depth 1 --branch v0.1.133 https://github.com/Wei-Shaw/sub2api.git
cd sub2api
```

路径都从 `backend/` 算起（前端不在本章范围）。本文基于 tag `v0.1.133`（commit `68901cb`）。它是个体量很大的项目（后端约 45 万行 Go，含 ent 生成代码和测试），本章只看后端 `internal/service/` 里的三个模块。

## 2. 部署形态与接入路径

sub2api 不是一个能本地 5 分钟跑通的库，它是一套要部署的服务。最短路径是 docker compose（仓库 `deploy/` 下有现成的 compose 文件）：

```bash
cd deploy
cp .env.example .env          # 配 PostgreSQL / Redis 连接、管理员账号等
docker compose up -d          # 拉起 backend + postgres + redis + 前端
docker compose logs -f sub2api
```

起来后管理面板在 `http://YOUR_SERVER_IP:8080`。典型接入路径是四步：

1. **加订阅账号**：管理后台里走 OAuth 登录，把你的 Claude / ChatGPT 订阅账号加进账号池（凭证存进 DB）。
2. **建用户、发 API Key**：给每个使用者建账号、发一把 `sk-` 开头的 API Key，设配额。
3. **客户端调用**：使用者拿这把 Key，像调 OpenAI / Anthropic 一样调网关的统一 endpoint：

```bash
curl http://YOUR_SERVER_IP:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-你发的key" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"ping"}]}'
```

4. **网关内部**：鉴权 → 从账号池按粘性策略选一个订阅账号（4.1 节）→ 必要时刷新它的 OAuth token（4.3 节）→ 翻译协议、转发（OpenAI 账号默认走 WS 桥，4.2 节）→ 计费、回流。

对使用者来说和调官方 API 没区别；网关在背后把"个人订阅"变成了"多租户 API"。

## 3. 全景架构

一次请求穿过网关的链路：

```mermaid
sequenceDiagram
    participant C as 客户端 Bearer Key
    participant G as gateway 路由 鉴权
    participant S as 账号调度器 三层粘性
    participant R as OAuth 刷新 并发安全
    participant F as 协议转发 HTTP 或 WS 桥
    participant U as 上游订阅后端
    C->>G: 1 请求 + API Key
    G->>S: 2 选一个订阅账号
    S->>R: 3 token 过期 先刷新
    R->>F: 4 带可用账号去转发
    F->>U: 5 HTTP 或 WebSocket
    U-->>F: 6 流式响应
    F-->>C: 7 翻成客户端协议回流 同时计费
```

后端代码分层（只列和本章相关的）：

| 区块 | 位置 | 职责 |
|---|---|---|
| 账号调度 | `internal/service/openai_account_scheduler.go`、`openai_sticky_compat.go` | 三层粘性选账号 |
| WS 桥接 | `internal/service/openai_ws_forwarder.go` | HTTP/SSE ↔ ChatGPT WebSocket |
| OAuth 刷新 | `internal/service/oauth_refresh_api.go`、`token_refresh_service.go`、`internal/pkg/openai/oauth.go` | 复用官方 client_id + 并发刷新 |
| 协议翻译 | `internal/pkg/apicompat/` | Anthropic / ChatCompletions / Responses 互转 |
| 数据模型 | `ent/`、`ent/schema/` | Ent ORM 生成的实体与查询 |

和本章相关的核心抽象有三个：

| 抽象 | 位置 | 一句话定位 |
|---|---|---|
| 三层调度层级常量 | `openai_account_scheduler.go:21-23` | `previous_response_id` / `session_hash` / `load_balance` 三档粘性 |
| WS forwarder | `openai_ws_forwarder.go` | 把上游 WebSocket 流桥成对客户端的 SSE 流 |
| OAuthRefreshAPI | `oauth_refresh_api.go:34-39` | 进程内锁 + 分布式锁的并发刷新控制器 |

下面三节按"怎么选账号 → 怎么转发 → 怎么刷 token"的顺序拆开。

## 4. 核心模块

### 4.1 三层粘性会话调度

**一句话总结**：上游有状态时不能随便负载均衡。sub2api 选账号分三层，从"最该粘"到"可以随便挑"递进：先看上游返回的会话 id 能不能粘回原账号，不行再看请求内容 hash 能不能粘，再不行才走负载均衡。

#### 4.1.1 问题：续聊请求换了账号就失效

OpenAI 的 Responses API 是有状态的：模型回一次，给你一个 `response_id`；你下一轮带上 `previous_response_id`，上游就能接着上次的上下文聊。但这个 id 是**绑在生成它的那个订阅账号上的**：会话状态存在那个账号的服务端，换一个账号，id 立刻失效，续聊直接报错。

所以账号池调度不能像无状态后端那样纯负载均衡。同一个会话的连续请求，必须尽量回到同一个账号。但又不能粘死，那个账号要是挂了、满了，得能退化到别的账号。

#### 4.1.2 纯负载均衡 vs 一致性哈希 vs 三层递进粘性

| 方案 | 怎么做 | 优点 | 代价 |
|---|---|---|---|
| 纯负载均衡 | 每次请求按负载挑账号 | 账号利用最均匀 | 续聊会换账号，`previous_response_id` 失效 |
| 一致性哈希 | 按会话 key 哈希到固定账号 | 同会话天然粘同账号 | 账号增减会大面积重新映射；账号挂了不好退化 |
| 三层递进粘性（sub2api） | 先按上游 id 粘 → 再按内容 hash 粘 → 都不命中才负载均衡 | 续聊优先粘、又能优雅退化 | 要维护 id→账号、hash→账号两套映射缓存 |

#### 4.1.3 sub2api 的选择：三层从强到弱兜底

三层的层级名直接写成了常量（`openai_account_scheduler.go:21-23`）：

```go
const (
    openAIAccountScheduleLayerPreviousResponse = "previous_response_id"  // 第一层：最强粘性
    openAIAccountScheduleLayerSessionSticky    = "session_hash"          // 第二层：会话哈希粘性
    openAIAccountScheduleLayerLoadBalance      = "load_balance"          // 第三层：兜底负载均衡
)
```

`Select` 方法按这个顺序从强到弱试，命中就返回：

```mermaid
graph TB
    A[请求进来 选账号] --> B[带了 previous_response_id 吗]
    B -->|带了| C[查这个 id 绑定的账号 还可用就用它]
    B -->|没带| D[算 session_hash]
    C --> E[命中 返回 跳过后两层]
    D --> F[查 session_hash 绑定的账号 可用就用]
    F -->|命中| G[返回]
    F -->|未命中| H[走负载均衡 按负载 错误率 首字延迟评分挑]
```

#### 4.1.4 关键代码：第一层 id 绑定与第二层哈希粘性

`Select` 的主干就是三段顺序判断（`openai_account_scheduler.go:265-323`，删去了日志和指标字段）：

```go
if previousResponseID != "" {
    selection, err := s.service.selectAccountByPreviousResponseIDForCapability(
        ctx, req.GroupID, previousResponseID, req.RequestedModel, /* … */)
    // …账号不支持本次要求的传输方式（HTTP / WS）就放弃这次命中
    if selection != nil && selection.Account != nil {
        decision.Layer = openAIAccountScheduleLayerPreviousResponse
        if req.SessionHash != "" {
            _ = s.service.BindStickySession(ctx, req.GroupID, req.SessionHash, selection.Account.ID)
        }
        return selection, decision, nil
    }
}

selection, err := s.selectBySessionHash(ctx, req)
if selection != nil && selection.Account != nil {
    decision.Layer = openAIAccountScheduleLayerSessionSticky
    return selection, decision, nil
}

selection, candidateCount, topK, loadSkew, err := s.selectByLoadBalance(ctx, req)
decision.Layer = openAIAccountScheduleLayerLoadBalance
```

三层各做一件事：

- **第一层**：请求带了 `previous_response_id`，就查这个 response 当初落在哪个账号，账号可用就直接返回。命中时顺手把 `session_hash` 也绑到这个账号（`:292-294`），下一次即使客户端没带 id，第二层也能接住。
- **第二层** `selectBySessionHash`：新会话或不走 Responses 的请求没有 id，就按请求内容算一个 `session_hash`，查它之前粘过的账号。
- **第三层** `selectByLoadBalance`：前两层都没命中，才按实时负载、错误率、首字延迟给候选账号打分，挑最优的。

这里有个容易被忽略的工程细节：`session_hash` 的算法升级过一次，sub2api 用双写双读扛过了迁移。`openai_sticky_compat.go:39-49` 的 `deriveOpenAISessionHashes` **同时算新旧两种哈希**（新格式用 xxhash，旧格式用 SHA256）；读缓存时（`:122-152`）先查新键、未命中再降级查旧键。

为什么要这么麻烦？因为 `session_hash` 是用作缓存 key 的，如果直接换算法，上线那一刻所有正在进行的老会话，新算出来的 hash 跟缓存里存的旧 hash 对不上，粘性瞬间全断、续聊集体失效。双写双读让老会话还能按旧 hash 找回原账号，平滑过渡。改"用作缓存 key 的哈希算法"都会遇到这个迁移问题，双写双读加降级是常见解法。

#### 4.1.5 取舍：粘性优先 vs 利用率均匀

三层粘性的代价是账号利用率不如纯负载均衡均匀，热点会话会持续压在某个账号上。sub2api 接受这个不均匀，因为对有状态上游，"续聊不断"比"负载均匀"重要得多。它把负载均衡降到第三层，只在前两层都不适用时才追求均匀。

对比纯负载均衡（适合无状态后端，如普通 API key 池）和一致性哈希（同会话粘同账号，但账号增减会大面积重映射、且账号故障时退化不优雅），三层递进的好处是**退化路径清晰**：强粘性不可用就自动落到弱粘性，弱粘性不可用就落到负载均衡，每一层都有下一层兜着。判断要不要用：上游有没有状态。有状态就别纯负载均衡。

#### 4.1.6 自己写一个最小三层粘性

核心是两套映射缓存，按强到弱依次试。Python 骨架约 30 行：

```python
class StickyScheduler:
    def __init__(self, accounts, id_cache, hash_cache):
        self.accounts = accounts
        self.id2acct = id_cache      # previous_response_id -> account_id
        self.hash2acct = hash_cache  # session_hash -> account_id（两张表分开，键空间不会互相污染）

    def select(self, req) -> str:
        # 第一层：上游返回的 id 最强粘性
        prev = req.get("previous_response_id")
        h = self.session_hash(req)
        if prev and (aid := self.id2acct.get(prev)) and self.alive(aid):
            self.hash2acct.set(h, aid)      # 顺手绑 hash，下次没带 id 也能粘回来
            return aid
        # 第二层：内容哈希粘性
        if (aid := self.hash2acct.get(h)) and self.alive(aid):
            return aid
        # 第三层：负载均衡
        aid = self.by_load()
        self.hash2acct.set(h, aid)          # 记住这次的粘性
        return aid

    def on_response(self, req, resp, aid):
        # 把上游新返回的 response_id 绑到这个账号，供下次续聊命中第一层
        if rid := resp.get("id"):
            self.id2acct.set(rid, aid)
```

要做到生产级，再补两件：哈希算法升级时的双写双读降级（4.1.4），以及 `alive()` 里接上账号的冷却 / 错误率状态。

### 4.2 HTTP 入、WebSocket 出的协议桥接

**一句话总结**：OpenAI 账号的上游可以走 HTTP/SSE，也可以走 Responses API 的 WebSocket 模式。sub2api 默认开 WS，好处是长连接可以按账号池化复用；代价是大多数客户端只会发 HTTP 请求，网关得在中间架一座桥。桥上最要紧的一条规则是：首个 token 出来之前不写响应头。

#### 4.2.1 问题：客户端讲 HTTP，上游走 WebSocket

sub2api 给每个 OpenAI 账号单独决定上游传输方式（`openai_ws_protocol_resolver.go:10-12` 定义了 `http_sse`、`responses_websockets`、`responses_websockets_v2` 三种）。WS 默认开启，可以全局或按账号强制回到 HTTP（`internal/config/config.go:717` 的注释写的是"默认开启，可按需回滚到 HTTP"）。

选 WS 是为了复用连接：每个账号维护一个上游 WS 连接池，可以预热、限制并发连接数，请求来了直接 `Acquire` 一条，省掉每次建连。但客户端通常是 OpenAI SDK、curl 这类 HTTP 客户端，发一次 POST，等一个 SSE 流。于是网关要把一次 HTTP 请求变成一次 WS 往返：发一个 `response.create` 帧，再把回来的事件逐个翻成 SSE。

还有一个时间窗口问题。上游可能在吐出任何 token 之前就出错，比如连接被关、读事件失败、上游 5xx。HTTP 响应头一旦发出去，网关就没法再重试，也没法改成一个干净的错误响应了。

#### 4.2.2 三种接法：HTTP 反代、WS 透传、协议桥接

这三种接法 sub2api 其实都有，分别对应不同的客户端和上游组合：

| 接法 | 适用组合 | sub2api 里的位置 |
|---|---|---|
| HTTP/SSE 反向代理 | 上游走 `http_sse` | WS 关闭或账号强制 HTTP 时的默认路径 |
| WS 端到端透传 | 客户端自己就连 WS | ingress `passthrough` 模式（`openai_ws_forwarder.go:2440`） |
| HTTP→WS 协议桥接 | 客户端 HTTP、上游 WS | `forwardOpenAIWSV2`（`openai_ws_forwarder.go:1722`） |

前两种实现简单，但各自有前提：反代要求上游也走 HTTP，放弃连接复用；透传要求客户端会讲 WS。本节讲第三种，因为只有它要处理两端协议形态的转换。

#### 4.2.3 sub2api 的选择：服务端代建 WS + 逐帧翻 SSE

入口 `forwardOpenAIWSV2`（`:1722-1810`）构建 WS URL、解析请求，再从连接池取一条到上游的连接。整条桥的数据流：

```mermaid
sequenceDiagram
    participant C as 客户端 HTTP
    participant F as forwarder
    participant U as 上游 WebSocket
    C->>F: 1 一次 HTTP POST
    F->>U: 2 取池化连接 发 response.create
    U-->>F: 3 response.created 等非 token 事件
    F->>F: 4 先缓冲 不写下游
    U-->>F: 5 首个 token 事件
    F-->>C: 6 写响应头 补发缓冲 再推当前帧
    U-->>F: 7 后续事件直到终端事件
    F-->>C: 8 批量 flush 成 SSE 收尾归还连接
```

#### 4.2.4 关键代码：首字前缓冲，为的是还能重试

上行是一次性把整个 `response.create` payload 发给上游（`:2001-2021`）。下行由 `emitStreamMessage`（`:2078-2095`）把每个 WS 事件加上 `data: ` 前缀和双换行，写成 SSE 帧。

缓冲逻辑在事件循环里（`openai_ws_forwarder.go:2276-2304`，节选）：

```go
// 在首个 token 前先缓冲事件（如 response.created），
// 以便上游早期断连时仍可安全回退到 HTTP，不给下游发送半截流。
shouldBuffer := firstTokenMs == nil && !isTokenEvent && !isTerminalEvent
if shouldBuffer {
    bufferedStreamEvents = append(bufferedStreamEvents, buffered)
} else {
    flushBufferedStreamEvents(eventType)
    emitStreamMessage(message, isTerminalEvent)
}
```

- **首个 token 之前**：非 token、非终端事件只进缓冲，下游一个字节都没收到。
- **首个 token 到达**：先把缓冲的事件按原顺序补发，再发当前事件。前导事件不丢。
- **之后**：按批量 flush，默认攒 4 帧或等 25ms 刷一次（`:49-50`）。

缓冲换来的是重试的机会。调用方在每次失败后先检查下游有没有被写过（`openai_gateway_service.go:2889-2891` 的 `c.Writer.Written()`），没写过才进入重试：

- **可重试的原因**（`read_event`、`dial_failed`、`upstream_5xx` 等）：按退避重连，受总时间预算约束（`openai_gateway_service.go:594-632` 的 `classifyOpenAIWSReconnectReason`）。
- **续链锚点失效**（`previous_response_not_found`）：去掉 `previous_response_id` 重放一次。
- **不可重试的原因**（`auth_failed`、`policy_violation` 等）：直接停。
- **重试用尽**：`writeOpenAIWSFallbackErrorResponse` 给客户端写一个完整的错误响应，而不是半截的 200 流。

注释里的"回退到 HTTP"是早期设计留下的说法。这个版本命中 WS 后不再自动切回 HTTP 传输（`openai_gateway_service.go:2778` 的注释写明了"不再自动回退 HTTP"），错误类型名 `openAIWSFallbackError` 保留了下来。

这条规则可以推广到边转发边处理的代理：**在第一个不可撤销的动作之前，留一个能反悔的窗口。**

#### 4.2.5 取舍：缓冲带来首字延迟，换可重试

代价是首字延迟略增：要等到第一个 token，而不是第一个事件，才开始回流。对要计费、要重试的网关，这个交换划算，因为发了一半就断的坏流，客户端和计费都难处理。如果你的代理只做透传、不计费、不重试，可以事件来了就发。

#### 4.2.6 自己写一个最小 HTTP→WS 桥

骨架抓三件事：代建 WS、首字前缓冲、逐帧翻 SSE。下面用 `aiohttp` + `websockets`（v13 以上）写，`is_token`、`is_terminal`、`is_error`、`build_payload` 按上游协议实现，这里省略：

```python
def sse(evt):
    return f"data: {json.dumps(evt)}\n\n".encode()

async def bridge(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    buffered = []                                   # 首字前的前导事件
    async with websockets.connect(UPSTREAM_WS, additional_headers=AUTH) as ws:
        await ws.send(json.dumps(build_payload(await request.json())))
        async for raw in ws:
            evt = json.loads(raw)
            if not resp.prepared:
                if is_error(evt):                   # 下游还没收到任何字节，可以干净报错或重试
                    return web.json_response({"error": evt}, status=502)
                if not (is_token(evt) or is_terminal(evt)):
                    buffered.append(evt)
                    continue
                await resp.prepare(request)         # 此刻才写响应头，不可逆
                for b in buffered:                  # 补发前导事件，顺序不变
                    await resp.write(sse(b))
            await resp.write(sse(evt))
            if is_terminal(evt):
                break
    if not resp.prepared:                           # 上游没给 token 就关了连接
        return web.json_response({"error": "upstream closed"}, status=502)
    return resp
```

要点在 `prepare()`：这个写响应头的动作推迟到首个 token，之前任何失败都还能换成重试或完整的错误响应。

### 4.3 复用官方 CLI 的 Client ID + 并发安全刷新

**一句话总结**：走 OAuth 不必自己申请 client_id，可以直接借用官方 CLI 的；而多副本部署时，同一个账号的 token 过期，几个副本会同时去刷新、把上游打出 `invalid_grant`，所以刷新要层层加锁去重，并在撞车时能恢复。

#### 4.3.1 问题：自研 client_id 拿不到，重复刷新会打挂上游

要走订阅账号的 OAuth，第一道坎是 client_id。厂商不会给第三方发"能登录 Codex / Claude Code 订阅"的 OAuth client。第二道坎更隐蔽：token 会过期，刷新用的 `refresh_token` 通常**一次性**（用一次就轮换成新的）。多副本部署时，几个副本同时发现 token 过期、同时拿同一个 `refresh_token` 去刷，第一个成功、其余的拿着已被消费的旧 token 去刷，上游回 `invalid_grant`，账号被标记异常。

#### 4.3.2 各副本各自刷 vs 单点刷新服务 vs 多层锁 + 竞争恢复

| 方案 | 怎么做 | 优点 | 代价 |
|---|---|---|---|
| 各副本各自刷 | 谁发现过期谁刷 | 简单 | 多副本撞车，`invalid_grant` |
| 单独的刷新服务 | 所有刷新走一个中心服务排队 | 无撞车 | 多一个单点服务，要部署维护 |
| 多层锁 + DB 重读 + 竞争恢复（sub2api） | 进程内锁 + 分布式锁串行化，加锁后重读 DB，撞车了能恢复 | 无需独立服务，多副本安全 | 锁逻辑较复杂 |

#### 4.3.3 sub2api 的选择：借官方 client_id + 四道防御

client_id 直接借官方的。`internal/pkg/openai/oauth.go:16-19`，注释都写明了来源：

```go
// OpenAI OAuth Constants (from CRS project - Codex CLI client)
const (
    // OAuth Client ID for OpenAI (Codex CLI official)
    ClientID = "app_EMoamEEZ73f0CkXaXp7hrann"
)
```

这是这类项目的通用做法：复用官方 CLI 公开的 client_id 走标准 OAuth flow，凭证落到自己的 DB。

并发刷新的控制器是 `OAuthRefreshAPI`（`oauth_refresh_api.go:34-39`），手里两把锁：一个 `sync.Map` 装进程内的 per-account 互斥锁，一个可选的 Redis 分布式锁。刷新时四道防御层层收口：

```mermaid
graph TB
    A[要刷新某账号 token] --> B[进程内锁 同进程内同账号串行]
    B --> C[Redis 分布式锁 跨副本同账号串行]
    C -->|锁被别的副本持有| D[直接返回 LockHeld 交给上层决定重试或跳过]
    C -->|拿到锁| E[从 DB 重读最新账号 防止用了缓存里的旧 token]
    E --> F[二次检查 是否仍需刷新 别人可能已经刷过]
    F --> G[执行 Refresh]
    G -->|成功| J[写回 DB 打 token 版本时间戳]
    G -->|失败 invalid_grant| H[重读 DB]
    H --> I[refresh_token 已变 说明别人刷成功了 用新的 标记竞争恢复]
```

#### 4.3.4 关键代码：加锁后重读 DB，撞车了也能恢复

四道防御的代码落点：

- **进程内锁**（`:56-64` `getLocalLock`）：为每个 `cacheKey` 返回唯一的 `sync.Mutex`，挡住同进程内多个 goroutine 并发刷同一账号。
- **分布式锁**（`:83-106`）：拿到进程内锁后，再抢 Redis 的"刷新中"锁；Redis 不可用就降级为只靠进程内锁；锁被别的副本持有就返回 `LockHeld=true`，让调用方决定重试还是跳过。
- **加锁后 DB 重读 + 二次检查**（`:108-126`）：拿到锁后**从 DB 重新读最新账号**，而不是用进程缓存里可能已过期的 `refresh_token`；再检查一次是否真的还需要刷（很可能在你抢锁的间隙，别的 worker 已经刷好了）。
- **invalid_grant 竞争恢复**（`:131-142`）：万一还是刷出了 `invalid_grant`（refresh_token 被别人消费了），重读 DB，发现 `refresh_token` 已经变了，就说明别的 worker 刷成功了：直接用更新后的账号返回，标记为"竞争恢复"而不是失败。

后两道是在补锁的漏洞。**锁只能减少撞车，不能消灭撞车**：Redis 不可用时会降级成只有进程内锁，锁 TTL 过期后也可能两个副本同时在刷。所以加锁后还要重读、二次检查，防住"我抢到锁时别人刚刷完"；`invalid_grant` 恢复则处理锁没拦住、真撞了车的情况。这套防御的统一入口是 `RefreshIfNeeded`（定义在 `oauth_refresh_api.go:75`）；上层的 `refreshWithRetry`（`token_refresh_service.go:265-296`）带重试地调它，成功后打一个 `_token_version` 时间戳落库。

#### 4.3.5 取舍：多层锁的复杂度 vs 不引入单点服务

最干净的去重方案其实是搞一个独立的"刷新服务"，所有刷新请求排队走它，天然无撞车。sub2api 没这么做，它选了"在每个副本里用锁 + DB 重读"把并发收口，代价是锁逻辑复杂（进程内、分布式两层，还要处理 Redis 挂掉的降级和恢复），收益是**不必多部署一个单点服务**。对一个本来就连着 PostgreSQL + Redis 的系统，复用现有 Redis 做分布式锁比新增一个服务划算。

这个取舍的判断点：撞车的代价有多高、你愿不愿意为消灭它引入新组件。`invalid_grant` 只是让账号短暂异常、能自动恢复，代价没高到值得专门起个服务，于是用锁 + 恢复兜住就够了。

sub2api 的分布式锁本身有一个常见的坑。它的实现是（`internal/repository/gemini_token_cache.go:41-49`）：

```go
func (c *geminiTokenCache) AcquireRefreshLock(ctx context.Context, cacheKey string, ttl time.Duration) (bool, error) {
    key := fmt.Sprintf("%s%s", oauthRefreshLockKeyPrefix, cacheKey)
    return c.rdb.SetNX(ctx, key, 1, ttl).Result()
}

func (c *geminiTokenCache) ReleaseRefreshLock(ctx context.Context, cacheKey string) error {
    key := fmt.Sprintf("%s%s", oauthRefreshLockKeyPrefix, cacheKey)
    return c.rdb.Del(ctx, key).Err()
}
```

锁的值固定是 `1`，释放时无条件 `Del`。设想副本 A 拿到锁，刷新卡住超过了 TTL，锁自动过期；副本 B 接着拿到锁开始刷；这时 A 刷完执行 `Del`，删掉的是 B 的锁，副本 C 又能进来。正确做法是把锁的值设成持有者自己的随机 token，释放时先比较再删除，而且比较和删除要原子（用 Lua 脚本）。Redis 官方文档讲单实例锁时给的就是这个做法。

在 sub2api 里这个坑的后果有限，因为后面还有重读和 `invalid_grant` 恢复兜着。但竞争恢复也不是万能的：RFC 6819（5.2.2.3 节）和 RFC 9700（OAuth 2.0 安全最佳实践，4.14 节）都建议授权服务器检测 refresh token 复用，一旦发现旧 token 被再次使用，可以吊销这一整族 token。上游如果这样实现，撞车一次账号就要重新授权，恢复逻辑重读 DB 也拿不到可用的新 token。

#### 4.3.6 自己写一个最小并发安全刷新

骨架抓三件事：带持有者 token 的分布式锁、加锁后重读、撞车恢复。Python + redis-py（`r` 是 Redis 客户端），`local_lock`、`db`、`do_refresh` 按你的存储实现：

```python
import uuid

UNLOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""

def refresh_if_needed(account_id):
    with local_lock(account_id):                        # 第一道：进程内锁
        key, owner = f"refresh:{account_id}", uuid.uuid4().hex
        if not r.set(key, owner, nx=True, px=30_000):   # 第二道：跨副本锁，值是持有者 token
            return {"lock_held": True}                  # 别人在刷，交给上层
        try:
            acct = db.reload(account_id)                # 第三道：加锁后重读 + 二次检查
            if not acct.token_expired():
                return acct                             # 别人刚刷过，直接用
            try:
                return do_refresh(acct)
            except InvalidGrant:                        # 第四道：撞车恢复
                fresh = db.reload(account_id)
                if fresh.refresh_token != acct.refresh_token:
                    return fresh                        # 别人刷成功了，用新的
                raise
        finally:
            r.eval(UNLOCK, 1, key, owner)               # 只删自己持有的锁
```

四道各管一种情况：进程内锁挡同进程内的并发（这里是线程），分布式锁挡其他副本，重读挡"别人刚刷完"，恢复挡"锁没拦住、真撞了车"。少了后两道，多副本下仍会偶发 `invalid_grant`。

## 5. 几个工程细节

读源码时记下的几个习惯：

| 习惯 | 项目怎么做 | 可迁移到哪 |
|---|---|---|
| 哈希算法升级双写双读 | `session_hash` 同时算 xxhash（新）和 SHA256（旧），读时先新键后降级旧键（`openai_sticky_compat.go:39-152`） | 要改缓存 key 哈希算法的系统，靠双读降级保证迁移期不断档 |
| 上游连接池化 | WS forwarder 从连接池 `Acquire` 上游连接，终端事件后安全回收 | 频繁建上游长连接的代理，池化省握手开销 |
| token 版本时间戳 | 刷新成功后写 `_token_version` 时间戳落库 | 多副本判断"谁的 token 更新"，时间戳比内容比对简单 |
| ent 代码生成 | 实体、查询全用 Ent ORM 生成（`ent/`、`ent/schema/`），业务只写 schema | 想要类型安全 + 少写样板 SQL 的 Go 项目 |
| 协议翻译按方向拆文件 | `apicompat/` 下 `anthropic_to_responses.go`、`chatcompletions_to_responses.go`、`responses_to_anthropic.go` 等，一个方向一个文件 | 多协议互转时按方向拆显式函数，是协议翻译类项目的通用思路 |

`apicompat/` 这套按方向拆的翻译文件，和一个常见疑问相关：为什么不抽一个统一中间 AST？答案和协议翻译类项目一致。协议是别人定义的、字段天天变，中间 AST 永远追不上，按方向写显式函数反而务实。每个方向独立一个文件，改一个不影响别的。

## 6. 适用边界与不该照搬的部分

### 6.1 什么场景该用 / 不该用

| 场景 | 建议 | 原因 |
|---|---|---|
| 学有状态后端的调度 / 协议桥接 / 并发刷新 | 适合 | 三个模式抽象干净，能直接搬到别的系统 |
| 自己内部小团队共享订阅、自担风险 | 看情况 | 技术上能跑，但要清楚 ToS 风险（下条） |
| 对外把订阅转售成商用 API | 不要 | 直接踩各家"账号共享 / 转售"的 ToS 红线，且依赖上游协议稳定，随时可能被风控或协议变更打断 |
| 想要一个无状态 API key 网关 | 不必用它 | 它的复杂度全在"有状态上游 + 多账号订阅"，无状态场景用 one-api / LiteLLM 更轻 |

sub2api 的核心价值绑在一个灰色的商业模式上，把个人订阅转成多人 API。这件事本身踩 ToS，且依赖上游不变（Responses 的 WS 模式、官方 CLI 的 OAuth client_id 一旦变更或加固，整条链路就断）。把它当生产基础设施前，这个脆弱性和合规风险要想清楚。**但这不影响三个工程模式的可迁移性**：它们和"卖订阅"无关，有状态后端的网关都用得上。

### 6.2 哪些模式可以照搬、哪些不要

**可以照搬**：

- 三层递进粘性调度：有状态后端的会话路由。
- HTTP→WS 桥接 + 首字前缓冲：两端协议形态不同、又要能重试的代理。
- 带持有者 token 的锁 + 重读 + 竞争恢复的并发刷新：多副本共享会过期凭证的系统。
- 哈希算法升级的双写双读降级：要改缓存 key 算法的系统。

**不要照搬**：

- 把订阅转售的商业模式本身（ToS + 脆弱性，见上）。
- 直接复用官方 client_id 这件事，技术上能用，但合规上是借用别人的 OAuth 客户端身份，自建产品时要评估风险。

## 7. 串起来落地时的两个判断

三个模块的骨架在 4.1.6 / 4.2.6 / 4.3.6，按"粘性调度 → 协议转发 → 并发刷新"的顺序拼，1-2 周能跑通一个"有状态上游 + 多账号"的迷你网关。这里只补两个串起来时容易踩、前面没强调的点：

- **每一步都用一个针对性测试卡住回归**：加粘性时，每加一层就测一次"续聊会不会断到别的账号"；做并发刷新时，开两个副本压测，并把锁 TTL 调得比刷新耗时还短，确认锁过期后不会互删、也不再偶发 `invalid_grant`。这两类 bug 不压测暴露不出来。
- **别从业务层开始**：用户体系、计费、支付是 SaaS 业务层，和三个核心模式正交，先别碰。先把"有状态上游怎么粘账号"跑通。面向无状态 API key 的多账号网关没有这个问题，这正是 sub2api 多出来的那部分工作。

## 8. 延伸阅读

- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（本书第 1 章）：同样把订阅版 CLI 包成 API，但定位是"单人本地用"，没有多租户、计费、有状态调度这套。两者对照能看清"本地代理"和"多人 SaaS"在工程上差了哪些量级。
- [songquanpeng/one-api](https://github.com/songquanpeng/one-api) 与 [Calcium-Ion/new-api](https://github.com/Calcium-Ion/new-api)：同为多账号 LLM 网关，但面向的是无状态的 API key 渠道，看它们的 channel 调度和计费模型，能反衬出 sub2api 为"有状态订阅"多做的那些事。
- [BerriAI/litellm](https://github.com/BerriAI/litellm)：协议翻译做得更全的 Python 网关，对照它能看 `apicompat/` 按方向拆 vs 统一 IR 两种翻译架构。
- 想系统学网关层的鉴权、计费、限流、渠道管理，可看本书作者的《AI Token 中转站实战》。
