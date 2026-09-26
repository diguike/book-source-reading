# 源码精读

> 一本持续追加的书。每一章拆一个开源仓库，目标是让你读完既会用、又能自己写个 mini 版，还能把里面的工程模式搬到自己项目里。

在线阅读：**[inferloop.dev/source-reading](https://inferloop.dev/source-reading)**

## 这本书和别的源码分析有什么不一样

大部分源码分析文章停在“它做了什么”：把调用链画一遍，把关键函数翻译一遍。读的时候很爽，合上就忘，因为它没回答真正有用的三个问题：为什么这么设计？别的项目怎么选的？换你来写从哪下手？

每一章会带你做到三件事：

1. **用起来**：跑通项目的最短路径，或者清楚的接入方式
2. **看懂核心模块**：挑 2 到 4 个模块，讲清它要解决的问题、还有哪些做法、项目为什么这么选，关键代码精确到行
3. **搬得走**：哪些做法可以用到你的项目里，哪些不该照搬；值得时给出一个实际跑过的最小实现

每章开头写明读完能带走的工程做法（也在 frontmatter 的 `learning_objectives` 里）。章节结构跟着项目走，不套统一模板。

所有源码引用都带 `path/to/file.ext:行号`，并锁定到具体的 git tag（项目没打 tag 时锁 commit），不引 main 分支——上游一改，行号就对不上了。

## 写给谁看

适合：

- 有 2 年以上工程经验，想从会用进阶到能造
- 关注 AI Agent、LLM Infra、开发者工具这几个方向的开源项目
- 习惯先翻源码再看文档

不适合：

- 刚开始学编程的（每章默认你能读懂目标项目用的语言）
- 只想找现成方案复制粘贴的（书里的最小实现用来讲清做法，不是生产组件）

## 目录

每一章是一个独立的开源仓库精读，互不依赖，任意顺序读都行。

- [前言](book/00-preface.md)

**已收录**

| 章节 | 精读的项目 | 核心讲什么 |
|---|---|---|
| [第 1 章　CLIProxyAPI：把藏在 CLI 后面的模型还原成标准 API](book/01-cliproxyapi.md) | [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | 不靠中间结构体的 N×M 协议翻译矩阵；带配额会过期的凭证池怎么调度；长驻服务的零停机热更新 |
| [第 2 章　Superpowers：用 Prompt 把 Agent 训成一个有纪律的工程师](book/02-superpowers.md) | [obra/superpowers](https://github.com/obra/superpowers) | 用 description 触发的渐进式技能加载替代巨型 system prompt；把 prompt 当对抗性测试来写，堵住 Agent 的合理化绕过 |
| [第 3 章　sub2api：把有状态的订阅后端做成多账号 API 网关](book/03-sub2api.md) | [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api) | 三层递进的粘性会话调度；HTTP 入 WebSocket 出的协议桥接；OAuth token 池的并发安全刷新 |
| [第 4 章　claude-tap：抓 AI Coding Agent 流量的本地代理](book/04-claude-tap.md) | [liaohch3/claude-tap](https://github.com/liaohch3/claude-tap) | 反向代理和自签 CA 正向代理怎么选；SSE 流式响应的边走边录重组；用策略表抹平 N 个 client 的适配差异 |
| [第 5 章　botmux：把 AI 编程 CLI 投影到飞书的会话编排器](book/05-botmux.md) | [deepcoldy/botmux](https://github.com/deepcoldy/botmux) | 三个正交变化轴各做一个适配器；编排进程重启不杀子进程；把别人写的 TUI 投影成可交互 Web 终端 |
| [第 6 章　LLM Space：给 Agent 开发者的本地调试工作台](book/06-llm-space.md) | [deer-flow/llm-space](https://github.com/deer-flow/llm-space) | 在没有原生流的通信边界上重建异步迭代器；一套 UI 跑两个运行时；把细粒度事件流折叠成可增量渲染的快照 |
| [第 7 章　vercel-labs/skills：Agent Skill 的规范、版本与分发](book/07-vercel-skills.md) | [vercel-labs/skills](https://github.com/vercel-labs/skills) | 规范三层结构不一致时以哪层为准；没有 version 字段怎么做版本控制；一份内容投给 N 个工具目录；内网自建分发源 |
| [第 8 章　claude-for-legal：一个垂直行业的 AI 能力怎么组织](book/08-claude-for-legal.md) | [anthropics/claude-for-legal](https://github.com/anthropics/claude-for-legal) | 垂直领域能力怎么切成插件和技能；私有知识层的模板与实例分离；知识库自进化闭环；多 Agent 编排的权限分层与供应链门禁 |
| [第 9 章　ATBClone：macOS 应用多开的身份改写与启动劫持](book/09-atbclone.md) | [aitobox/ATBClone](https://github.com/aitobox/ATBClone) | 基于能力探测选择参数隔离或应用包改写；用原生启动器重定向进程状态；重签名、Sandbox 与提权删除的安全边界 |

后续收录的项目会持续追加到这里。完整目录也见 [SUMMARY.md](SUMMARY.md)。

**附录**

- [附录 A　跨章模式索引](book/90-pattern-index.md)：按问题查，把九章里反复出现的工程问题串起来，对照不同项目的解法和选型判据

## 收录标准

热门不等于值得精读。进这本书要同时满足四条：

1. **star ≥ 5K，或在领域内有明确影响力** — 避免写完没人关心
2. **核心模块 10K 行代码以内** — 读得动，也讲得清
3. **设计上有真正的取舍** — 纯 CRUD、纯封装、纯算法实现的项目不收，因为没有可讲的选择
4. **能提炼出脱离本项目也成立的工程做法** — 是做法，不是项目特性；数量按项目实际，不凑数

代码质量太差的不收，读完只会让人沮丧。

欢迎在 issue 里提名候选项目，最终写哪些由作者判断。

## 关于作者

[diguike](https://github.com/diguike) — Agent 工程师，全栈工程师出身，方向是 Agent 自进化、长程 Agent、AI 资产建设（skill / prompt / 知识库 / 评测集）和团队 AI 体系搭建。

其他作品（均可在 [inferloop.dev](https://inferloop.dev) 在线阅读）：

| 书 | 讲什么 |
|---|---|
| [Hermes Agent 源码解读](https://inferloop.dev/hermes-agent) | 一个 Agent 的架构设计从头拆到尾 |
| [OpenClaw 源码解析](https://inferloop.dev/openclaw) | 现代 Agent 系统的架构与工程实践，含可运行的 Mini OpenClaw |
| [Agent Memory 工程实战](https://inferloop.dev/claude-mem) | 从 claude-mem 源码到企业级记忆平台 |
| [AI Token 中转站实战](https://inferloop.dev/llm-gateway) | 从 0 搭一个企业级 LLM 网关 |
| [百万级 AI Agent 平台架构](https://inferloop.dev/enterprise-agent) | 智能客服 SaaS 的完整架构实战 |
| [Agent 评测工程实战](https://inferloop.dev/agent-evals) | 怎么给 Agent 建一套能落地的评测体系 |

## 关于引用

书中引用的开源项目版权归各自作者所有。每处源码引用都标注了文件路径、行号和对应的 git tag，方便对照原仓库核实。发现引用错误或行号对不上，欢迎提 issue。
