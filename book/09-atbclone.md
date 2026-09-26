---
title: 第 9 章　ATBClone：macOS 应用多开的身份改写与启动劫持
project_url: https://github.com/aitobox/ATBClone
based_on_tag: v1.2.0
based_on_commit: c13656d
written_at: 2026-09-04
learning_objectives:
  - 能按目标程序的能力，在参数隔离、环境隔离和应用包改写之间按从浅到深的顺序选择
  - 能实现一个原生启动器，在不修改业务代码的情况下重定向数据目录等进程环境
  - 能解释 macOS 应用身份、代码签名、entitlements 与 App Sandbox 的关系，并划清兼容性改造和安全隔离的边界
  - 能为带删除和提权能力的本地工具设计可信状态、路径约束、输入转义与失败回滚
feishu_url: "https://fivwvysqdz.feishu.cn/wiki/BCQYwMfhciQASMkn1T4cl3K3nvh"
last_synced: "2026-09-04"
---

## 1. ATBClone

[ATBClone](https://github.com/aitobox/ATBClone) 是一个 macOS 应用多开工具。给它一个 `/Applications/WeChat.app`，它会生成带独立名称、Bundle ID 和数据目录的第二个 `.app`。Firefox 这类支持独立配置目录参数的程序可以只做一层启动包装（软分身）；微信、Telegram 等程序则要复制应用包、改身份、替换入口，再重新签名（硬分身）。

应用多开难在身份和状态，复制文件只是第一步。以飞书为例：把 `.app` 复制一份再打开，第二个进程什么窗口也不出就退了。原因在飞书内嵌的 Lark Framework 里：它自己实现了一套单实例逻辑，第二个进程启动时发现已有实例在跑，就通知第一个实例把窗口拉到前台，然后以退出码 34 退出（`src/atbclone/core/engines.py:557-566` 的注释记录了这个行为）。两个进程只要还共用同一个用户目录、同一个 Bundle ID 或同一把单实例锁，就会出现这种“打开了却没开出来”的情况，或者两个进程抢同一个数据库。反过来，改动应用包又会立刻破坏开发者签名，还可能让钥匙串、推送和系统权限失效。

ATBClone 把这组冲突压进了一条流水线：先探测目标应用，再用 Recipe 选择克隆策略，最后生成新的应用包并登记生命周期。它不是安全沙盒，不少兼容性手段会主动拆掉原应用的安全边界。所以这个仓库既能看到怎么让闭源桌面程序多开，也能看到系统工具在哪些地方要格外小心。

做 Agent 和开发工具的人会直接碰到这类问题。ATBClone 的内置 Recipe 里就有 Claude 桌面版、ChatGPT/Codex、VS Code、Zed：Claude 桌面版的规则除了隔离 `HOME`，还注入了 `CLAUDE_CONFIG_DIR`，Codex 的规则注入 `CODEX_HOME`（`src/atbclone/recipes/builtin/com.anthropic.claudefordesktop.yaml`、`com.openai.codex.yaml`），用来让两个账号的配置各自存放。要注意的是，克隆时它会把 `~/.claude`、`~/.claude.json`、`~/.codex` 整份复制进分身的数据目录（`src/atbclone/core/engines.py:125-177`），所以分身一开始就是主账号的副本，登录凭据也在里面。往外推一步，下面这些场景用的是同一组做法：

- 给一个桌面 Agent 开多个隔离的工作区；
- 写一个要兼容几十种目标程序的安装器；
- 让 Agent 在隔离环境里拉起别人的程序；
- 写一个需要提权、会删目录的本地 CLI。

本章讲其中四个可以带走的做法：按能力探测选策略、用原生启动器注入进程环境、改包后重建签名链、给带提权和删除能力的工具守住路径边界。

路径都从仓库根目录算起。本文锁定 tag `v1.2.0`、commit `c13656d`。Python 代码约 1.3 万行，其中非 GUI 部分约 7800 行。

## 2. 五分钟跑起来

ATBClone 只能在 macOS 上完成真实克隆。源码安装实际需要 Python 3.12 以上：`pyproject.toml` 声明的是 3.10，但 `src/atbclone/core/engines.py:301` 在 f-string 表达式里用了反斜杠，这是 3.12 才允许的写法。在 3.11 下，`atbclone --help` 直接报错：

```text
SyntaxError: f-string expression part cannot include a backslash
```

另外，硬分身还依赖 `clang`、`codesign`、`PlistBuddy` 和 LaunchServices（macOS 登记和打开 `.app` 的系统服务）。

```bash
git clone --branch v1.2.0 https://github.com/aitobox/ATBClone.git
cd ATBClone
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

先检查系统工具，再探测目标应用：

```bash
atbclone doctor
atbclone probe /Applications/Firefox.app
```

`probe` 不会修改应用。它读取 `Info.plist`、签名授权和 Framework 目录；对 Chromium、Electron、Firefox 以外的程序，还会扫描可执行文件中的字符串，寻找可用的数据目录参数（`src/atbclone/core/app_prober.py:149-151`）。确认策略后再创建分身：

```bash
atbclone clone /Applications/Firefox.app \
  --name Firefox-Work \
  --data-dir "$HOME/ATBClone/Data/Firefox-Work"
```

`probe` 的结论不一定等于 `clone` 实际采用的策略。`probe` 只看探测结果，`clone` 却优先使用内置 Recipe（`src/atbclone/cli/cmd_clone.py:64-65`）。Chrome 就是例子：探测会建议软分身，而内置规则在提交 `e5c9d4d` 里改成了 `hard_clone`，所以 `clone` 实际会复制并改写整个应用包。想强制走软分身，加 `--strategy soft_clone`。

默认产物在 `~/ATBClone/Apps`，数据在 `~/ATBClone/Data`，不需要管理员权限。把 `--output-dir` 指到 `/Applications` 才会触发 macOS 管理员授权。学习源码时应先使用用户目录，并选择不含敏感数据的测试程序。

常用生命周期命令只有三条：

```bash
atbclone list
atbclone update Firefox-Work
atbclone remove Firefox-Work --keep-data
```

仓库的非 GUI 测试可以在非 macOS 环境运行。本章检查的提交上，用 Python 3.12 排除依赖 Toga 的 GUI 测试后共有 348 项通过。这个数字只能说明纯 Python 逻辑通过，不能替代 macOS 上的签名、Gatekeeper（打开应用时检查签名和来源的闸门）、TCC（管理摄像头、通讯录等隐私授权的系统组件）和实际启动测试。

## 3. 全景架构

ATBClone 先把应用变成一份结构化描述，再把描述和 Recipe 合并成 `CloneTask`，最后由两种 Engine 生成新的 `.app`。

如图 3-1 所示，CLI 和 GUI 共用同一套核心流水线。界面只负责收参数；探测、决策、构建和状态登记都在 core 层完成。

```mermaid
flowchart LR
    U[CLI 或 GUI] --> I[AppInspector 读取应用身份]
    I --> R{RecipeLoader 命中规则}
    R -->|命中| T[CloneTask]
    R -->|未命中| P[AppProber 动态探测]
    P --> T
    T --> S{strategy}
    S -->|soft_clone| SE[SoftCloneEngine 生成轻量外壳]
    S -->|hard_clone| HE[HardCloneEngine 复制并改写应用包]
    SE --> X[Runner 执行构建脚本]
    HE --> X
    X --> A[新的 app 和独立数据目录]
    A --> M[StateManager 登记 clones.yaml]
```

图 3-1：从目标应用到分身应用的数据流

核心抽象只有五个：

| 抽象 | 源码位置 | 职责 |
|---|---|---|
| `AppInfo` | `src/atbclone/core/models.py:5-15` | 描述路径、Bundle ID、入口和沙盒状态 |
| `Recipe` | `src/atbclone/recipes/models.py:25-38` | 声明策略、参数、环境、代理和补丁开关 |
| `CloneTask` | `src/atbclone/core/clone_task.py:10-23` | 冻结一次克隆需要的输入 |
| `CloneEngine` | `src/atbclone/core/engines.py:14-448` | 把任务翻译成应用包改造步骤 |
| `CloneRecord` | `src/atbclone/core/state.py:11-25` | 保存后续更新和删除所需的状态 |

Recipe 是架构的中轴，每份 Recipe 描述一个目标程序该怎么适配：用软分身还是硬分身，数据目录从参数还是环境变量注入，要不要移除 Sandbox，要不要处理单实例锁。新增应用主要改 YAML，构建引擎保持不动。

## 4. 核心模块

### 4.1 能力探测驱动的策略选择

ATBClone 先看目标程序自己支持哪种隔离方式，再决定改造得多深。

#### 4.1.1 桌面程序把状态放在哪

桌面程序保存状态的方式没有统一答案。Chromium 接受 `--user-data-dir`，Firefox 接受 `-profile`，Electron 程序可能把路径落在 `app.getPath('userData')`，原生 Cocoa 程序还可能直接访问容器、Keychain 或硬编码目录。

因此多开有三条常见路线：

| 路线 | 怎么隔离 | 优点 | 代价 |
|---|---|---|---|
| 启动参数 | 给原二进制传独立 profile 路径 | 不改原应用，签名不变 | 目标程序必须明确支持参数 |
| 不用任何工具 | `open -n -a 应用 --args --user-data-dir=目录` | 零成本，适合临时试一次 | 每次手敲，Dock 里分不清是哪个实例 |
| 环境变量 | 改 `HOME`、`TMPDIR` 等进程环境 | 对闭源程序也能尝试 | Cocoa API 和硬编码路径未必遵守环境变量 |
| 应用包改写 | 复制 `.app`、改 Bundle ID 和入口 | Dock、LaunchServices、部分系统权限可分开识别 | 必须重签，兼容与安全代价最高 |

ATBClone 的优先级就是从浅到深。内置 Recipe 先覆盖已知应用；没有规则时，`AppProber.analyze()` 按 Framework、entitlements（签名里声明的权限清单）和二进制字符串生成临时 Recipe（`src/atbclone/core/app_prober.py:101-186`）。

```python
if app_type in ("chromium", "electron"):
    strategy = "soft_clone"
    launch_args = ["--user-data-dir={{ATB_DATA_DIR}}"]
elif app_type == "firefox":
    strategy = "soft_clone"
    launch_args = ["-profile", "{{ATB_DATA_DIR}}"]
else:
    strategy = "hard_clone"
    strip_sandbox = has_sandbox
    env_injection = {
        "HOME": "{{ATB_DATA_DIR}}/Home",
        "TMPDIR": "{{ATB_DATA_DIR}}/Tmp",
    }
```

代码节选自 `src/atbclone/core/app_prober.py:136-176`，tag `v1.2.0`。

这段决策树里可以带走的是降级顺序：有正式能力就用正式能力；没有才进入兼容性更差、侵入性更强的路径。

```mermaid
flowchart TD
    A[读取 Info.plist 与签名授权] --> B[识别 Chromium Electron Firefox Cocoa]
    B --> C{存在内置 Recipe}
    C -->|是| D[采用已验证规则]
    C -->|否| E{支持独立数据参数}
    E -->|是| F[软分身 参数隔离]
    E -->|否| G{原应用启用 Sandbox}
    G -->|否| H[硬分身 HOME 隔离]
    G -->|是| I[硬分身并移除 Sandbox]
```

图 4-1：从能力探测到克隆策略

#### 4.1.2 二进制字符串探测的可信度

`BinaryArgumentProber` 最多读取可执行文件前 15 MB，从可打印字符串里寻找候选参数（`src/atbclone/core/argument_prober.py:67-130`）。随后 `LaunchArgumentValidator` 先查 Framework 白名单，陌生参数再回到二进制字符串确认（`src/atbclone/core/argument_prober.py:205-292`）。

这比盲目塞参数稳一些，但仍可能误判。二进制里出现某个字符串，可能来自帮助文本、依赖库或已经废弃的分支，当前入口未必会读它。更可靠的能力系统应把证据分级：官方声明、已验证 Recipe、静态猜测。只有前两类可以自动执行，静态猜测最好停在建议层，并要求用户试运行确认。

可迁移到别的工具时，可以把策略选择写成显式评分。下面是示意伪码，ATBClone 里没有 `planner` 这个对象：

```python
evidence = [
    builtin_recipe(bundle_id),
    framework_capability(app_type),
    signed_entitlements(app_path),
    binary_string_hint(executable),
]

plan = planner.choose(
    evidence=evidence,
    prefer=["documented-flag", "verified-wrapper", "binary-rewrite"],
)
```

评分公式怎么定都行，要紧的是每个决策都能回答三件事：证据来自哪里，可信度多高，失败后降到哪一步。

### 4.2 启动器是进程环境的适配层

启动器不实现任何业务，只在 `exec` 之前改好进程的环境变量和参数，然后把自己替换成原程序。

#### 4.2.1 为什么不能一直用 Shell 脚本

最小启动器确实只有几行：导出环境变量，然后 `exec` 原二进制。问题是 macOS 把 `.app` 的入口当成真正的可执行文件参与签名和启动。某些应用还会检查入口类型、派生子进程，或者依赖通知中心和菜单栏识别当前二进制。Shell wrapper 的兼容性很快到顶。

ATBClone 实际用了三种入口，按目标程序的类型选：

- **软分身**：运行时编译一个 C 启动器（产物是 Mach-O，macOS 的可执行文件格式），它直接 `execv()` 原应用包里的二进制（`src/atbclone/core/engines.py:450-540`）。
- **Chromium / Electron 硬分身**：先把分身包里的原入口改名为 `.bin`，再放一个同名的 C 启动器去执行它（`engines.py:893-912`）。
- **Cocoa 硬分身**（且 Recipe 没有启动参数时）：不换入口，而是往分身包里的二进制副本加一个启动时自动运行的动态库，由它在程序初始化前设置环境变量（`engines.py:883-892`）。

这三种入口的差别，决定了分身能不能拥有自己的身份。结论先说：软分身的进程仍然认为自己是原应用。macOS 按可执行文件所在的位置确定 main bundle，软分身 `execv` 的是原应用包里的二进制，所以进程起来后 `NSBundle.mainBundle` 仍是原来那个 `.app`，读到的 Bundle ID 也是原应用的。分身包 `Info.plist` 里改过的名称和 Bundle ID（`engines.py:531-533`）只影响 Dock 和 LaunchServices 怎么显示、登记这个入口，挡不住按 Bundle ID 做的单实例检测和偏好读写。两种硬分身执行的都是分身包里的二进制，main bundle 才是分身自己。这也解释了为什么 Chrome 的内置规则后来从软分身改成了硬分身。

| 启动器形态 | 生成成本 | 系统兼容性 | 修改原应用 |
|---|---:|---:|---:|
| Shell wrapper | 最低 | 较低 | 软分身可不改 |
| 原生 C launcher | 需要 `clang` | 较高 | 硬分身要替换入口 |
| 动态库构造器（Cocoa 硬分身在用） | 需要改二进制副本的加载命令 | 取决于目标二进制 | 会改原二进制副本 |

C 启动器把 Recipe 中的环境变量写成 `setenv()`，把启动参数与用户传入的 `argv` 合并，最后调用 `execv()`。核心位于 `src/atbclone/core/engines.py:251-343`。

```c
int main(int argc, char *argv[]) {
    setenv("HOME", "/Users/me/ATBClone/Data/Chrome-Work/Home", 1);
    setenv("TMPDIR", "/Users/me/ATBClone/Data/Chrome-Work/Tmp", 1);

    char *new_argv[] = {argv[0], "--user-data-dir=...", NULL};
    execv(real_bin, new_argv);
    return 1;
}
```

上面是 `CloneEngine._build_c_launcher_compile_cmd()` 的示意缩写，不能直接编译。真正代码还保存原始 `HOME` 到 `REAL_USER_HOME`，供白名单软链接使用。

```mermaid
sequenceDiagram
    participant L as LaunchServices
    participant W as Clone Launcher
    participant O as Original Binary
    participant D as Clone Data Dir
    L->>W: 打开分身 app
    W->>W: 设置 HOME TMPDIR 语言 代理
    W->>W: 合并 Recipe 参数与 argv
    W->>O: execv 替换当前进程
    O->>D: 读写独立配置与缓存
```

图 4-2：启动器只改启动边界，随后把进程交给原程序

#### 4.2.2 HOME 隔离能挡住什么

把 `HOME` 指向另一个目录，只会影响愿意读取这个环境变量的代码。目标程序仍然以当前用户身份运行，理论上可以访问该用户原本有权访问的其他文件。它也可能通过 Foundation API、容器路径或绝对路径绕过 `HOME`。

ATBClone 还会为伪 Home 链接真实的 `Library/Keychains`，部分 Recipe 甚至链接 `.ssh`。微信规则就是如此（`src/atbclone/recipes/builtin/com.tencent.xinWeChat.yaml:1-11`）。加上第 1 节说的 Claude、Codex 配置整份复制，分身手里其实握着和主账号相同的凭据。这样做能减少登录或 Git 功能失效，却把最敏感的宿主凭据重新交给了分身。

所以启动器做的是状态分流，提供不了安全隔离。真正需要安全边界时，应使用 App Sandbox、独立 macOS 用户、容器或虚拟机。启动器适合解决多账号和文件锁冲突，不适合运行不可信代码。

### 4.3 改一个字节，整条签名链都要重建

代码签名把应用身份、权限和完整性绑在一起，改了包里任何受保护的内容，这三样都要重新建立。

#### 4.3.1 Bundle ID 与代码签名解决不同问题

修改 `CFBundleIdentifier` 能让 LaunchServices 把分身视作另一个应用，也会影响偏好设置、TCC 和容器寻址。但 Bundle ID 只是声明。代码签名还绑定可执行文件、资源、Team ID 和 entitlements。替换入口、改 plist 或修改 Framework 后，原开发者签名都会失效。

常见处理方式有三种：

| 方式 | 身份连续性 | 能否修改应用包 | 适合场景 |
|---|---|---|---|
| 保留原签名 | 完整 | 不能改受保护内容 | 只用启动参数的软分身 |
| ad-hoc 重签 | 丢失开发者身份 | 可以 | 本机实验和开发工具 |
| 自有证书重签 | 变成新开发者身份 | 可以 | 有完整发布、授权和公证流程的产品 |

ATBClone 对生成的包执行 ad-hoc 重签，也就是 `codesign --sign -`。硬分身会先给 dylib、可执行文件、Framework、appex（App Extension，打包在应用里的扩展）和子应用逐层签名，再签最外层 bundle，最后执行严格校验（`src/atbclone/core/engines.py:984-1024`）。

```mermaid
flowchart TD
    A[复制原 app] --> B[修改 Bundle ID 与显示名]
    B --> C[替换入口或注入 dylib]
    C --> D[原开发者签名失效]
    D --> E[清理扩展属性]
    E --> F[内部 Mach-O 与 Framework 重签]
    F --> G[子 app 和 appex 重签]
    G --> H[最外层 app 重签]
    H --> I[codesign strict verify]
```

图 4-3：应用包改写后的签名重建顺序

#### 4.3.2 移除 Sandbox 的代价

如果 Recipe 设置 `strip_sandbox: true`，引擎先从原应用提取 entitlements，再删掉 App Sandbox 开关和一组绑定原开发者团队的能力（应用组、钥匙串组、推送、关联域名、iCloud 等），最后用剩余内容重签（`src/atbclone/core/engines.py:958-999`）。

这一步解决的是新身份拿不到原团队专属 entitlement 的签名问题。代价也很直接：分身不再受原 App Sandbox 约束，访问范围可能扩大；与此同时，钥匙串组、推送、iCloud 和应用组功能可能失效。

仓库还提供两类更深的二进制补丁，只在 Recipe 打开开关或目标是飞书时才会执行：

- 一类针对开篇说的飞书单实例问题：在内嵌框架里找到那次“通知已有实例”的函数调用，把调用处和被调函数本身都改成直接返回 0，让每份副本都认为自己是主实例（`engines.py:557-626`）。改的是被调函数本身，所以框架里所有调用它的地方都受影响，不止启动那一处。
- 另一类修改 CEF 的 Seatbelt 和 GPU 相关分支（`src/atbclone/core/engines.py:556-677`）。

这些补丁靠字节模式搜索定位，目标应用升级后可能匹配失败，也可能命中错误位置。

安全判断应该分成两问：

1. 工具源码有没有明显的数据外传或后门？本章检查的 v1.2.0 没看到运行时遥测和主动上传逻辑。
2. 工具生成的分身是否保持原应用的安全属性？硬分身通常不能。原签名、Sandbox 和部分 entitlement 已被替换或删除。

不要把第一问的答案自动套到第二问。

### 4.4 更新和删除：状态文件决定删什么

能替用户递归删除目录的工具，状态文件里写的路径就决定了它会删掉什么，所以状态文件本身也是权限边界的一部分。

ATBClone 把 `clone_name`、源路径、目标路径、数据路径、Bundle ID 和代理摘要写进 `~/ATBClone/clones.yaml`（`src/atbclone/core/state.py:11-76`）。`update` 先删除旧应用包，再用原数据目录重新构建；`remove --with-data` 同时删除应用和数据。

数据和程序分开放，应用升级时只替换 bundle，账号和聊天数据留在外部目录。问题出在信任关系：删除命令直接采用状态文件里的 `dest_path` 和 `data_dir`，只做 shell quote，没有验证路径必须位于 ATBClone 管理根目录（`src/atbclone/cli/cmd_remove.py:59-77`）。

```mermaid
flowchart LR
    S[clones.yaml] --> P[读取 dest_path 和 data_dir]
    P --> A{路径是否在用户 Home 内}
    A -->|是| U[普通权限 rm -rf]
    A -->|否| R[请求管理员权限]
    R --> X[管理员权限 rm -rf]
    U --> M[删除状态记录]
    X --> M
```

图 4-4：当前删除流程把状态文件内容直接带进执行层

更稳的实现要在执行前重新建立边界：

下面是示意代码，`UnsafePath` 是自定义异常：

```python
def assert_managed_path(path: Path, roots: list[Path]) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if resolved in {Path("/"), Path.home()}:
        raise UnsafePath(resolved)
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        raise UnsafePath(resolved)
    return resolved
```

还应补四层保护：状态文件权限固定为 `0600`；代理密码不写入摘要（现在 `proxy_summary` 存的是带用户名密码的完整代理 URL，`update` 会读它，`list` 会原样打印，见 `src/atbclone/recipes/models.py:14-17`、`src/atbclone/core/clone_inspector.py:130-134`）；更新使用临时目录构建，校验成功后原子替换；提权时传结构化参数或固定脚本，不把用户输入拼进整段 root shell。

引擎里的路径大多用 `shlex.quote` 转义过，显示名称和 Bundle ID 却是拼进双引号 Shell 片段的。问题分三步：

1. `_build_display_name_cmd()` 只转义反斜线和双引号，双引号里的 `$()` 照样会执行（`src/atbclone/core/engines.py:80-94`）。
2. 目标应用的 Bundle ID 来自它自己的 `Info.plist`，也会被插进构建脚本（`engines.py:934-954`）。
3. 产物写进 `/Applications` 时，整段脚本以管理员权限运行。

三步连起来，克隆一个不可信的应用，就等于让它的 `Info.plist` 以管理员身份执行命令。显示名默认取自目标应用 `Info.plist` 里的 `CFBundleDisplayName`（`src/atbclone/core/app_inspector.py:74`），所以第 1 步的输入同样可能来自目标应用本身。在 Python 3.12 下可以复现：把显示名设成含 `$()` 的字符串，生成的命令执行后，括号里的命令确实会被执行。

Shell quoting 只能保护被正确放进一个 Shell 参数的位置。只要代码先拼模板、再让 `shell=True` 解释整段文本，就要逐个追踪数据来自哪里、落在哪种语法上下文。路径、PlistBuddy 命令、C 字符串和 AppleScript 是四种不同上下文，不能靠一个 escape 函数通吃。

## 5. 能搬走的工程习惯

| 习惯 | ATBClone 的做法 | 搬到其他项目时怎么用 |
|---|---|---|
| 先探测再决策 | `AppInspector` 与 `AppProber` 先生成结构化事实 | 安装器、迁移工具、驱动管理器先产出 capability report |
| 策略与执行分离 | YAML Recipe 描述差异，Engine 只解释规则 | 多平台构建、IDE 适配、数据导入器可用同一模式 |
| 侵入性逐级升级 | 参数隔离优先，最后才改二进制 | 自动修复工具应优先可逆、可验证的动作 |
| 数据与程序分开 | clone bundle 可重建，data dir 独立保留 | 桌面应用更新和插件更新都可减少迁移风险 |
| 构建后校验 | 最后执行 `codesign -vv --deep --strict` | 任何会改制品的工具都要验证最终产物而非中间步骤 |

## 6. 适用边界与不该照搬的部分

ATBClone 适合个人在受控 Mac 上做多账号、测试配置和应用兼容性实验。软分身尤其适合本来就支持 profile 参数的浏览器和编辑器，因为它不需要复制、反汇编或拆掉原应用沙盒。

硬分身不适合以下场景：运行来源不明的应用；承载主力企业账号、支付或密码管理数据；把独立代理当作匿名或防关联保证；要求保留原厂签名、推送、Keychain Group 和 App Store 更新链路。

四个实现细节不应直接照搬：

- 不要把清除 quarantine（下载文件带的来源标记，Gatekeeper 据此拦截）当成普通构建步骤。`xattr -cr` 会同时移除安全来源信息。
- 不要默认把真实 Keychains 或 `.ssh` 链回伪 Home。兼容性提高了，隔离边界也被打穿。
- 不要把 `HTTP_PROXY` 和 `HTTPS_PROXY` 当成全流量代理。应用可以忽略环境变量，DNS、QUIC、UDP 或自建网络栈也可能绕开。
- 不要在自动探测到 Sandbox 后直接移除它。应展示风险、要求明确确认，并优先建议软分身或独立系统用户。

源码审计也不能证明 Release 里的 DMG 必然由同一份源码构建。高敏感环境应从锁定提交自行构建，并核对签名、公证和最终制品哈希。

## 7. 自己实现一个最小版本

一个教学版不需要二进制补丁、动态库注入和管理员权限。把范围限制为支持 profile 参数的应用，约一两百行 Python 就能完成。

第一步只支持两种适配器：Chromium 的 `--user-data-dir` 和 Firefox 的 `-profile`。按 4.2 节的分析，这种软分身的进程身份仍是原应用，只适合能靠参数分开数据目录的程序。下面是示意代码，`inspect_plist`、`Launcher` 等都是待实现的函数和类：第二步读取 `Info.plist`，生成新的 Bundle ID 与轻量 `.app`。第三步写一个启动脚本或预编译的小型 launcher。最后把记录写入只允许当前用户读取的 JSON 文件。

```python
def build_soft_clone(source_app: Path, dest_app: Path, data_dir: Path):
    info = inspect_plist(source_app / "Contents/Info.plist")
    adapter = choose_adapter(info.bundle_id, source_app)

    dest_app.mkdir(parents=True)
    copy_info_plist(source_app, dest_app)
    rewrite_bundle_id(dest_app, info.bundle_id + ".profile.work")

    launcher = Launcher(
        executable=source_app / "Contents/MacOS" / info.executable,
        args=adapter.profile_args(data_dir),
        env={},
    )
    launcher.write_to(dest_app / "Contents/MacOS" / info.executable)
    sign_and_verify(dest_app)
```

这个 mini 版故意不处理通用 Cocoa 应用。遇到不支持数据目录参数的目标就明确报错，不自动改 `HOME`，更不移除 Sandbox。这样它能守住一条承诺：只生成启动入口，不改目标程序的安全模型。

如果继续扩展，顺序应该是：先加入 dry-run，输出即将修改的文件和命令；再加入临时目录构建与原子替换；然后做路径约束和恶意输入测试；最后才考虑 hard clone。二进制 patch 应作为单独实验模块，按目标版本和哈希精确启用，不能依赖模糊字节搜索自动运行。

## 8. 延伸阅读

- [WeChatTweak](https://github.com/sunnyyoung/WeChatTweak)：同样解决微信多开，走的是另一条路——原地给应用打补丁，不复制应用包。和 ATBClone 的复制加重签对照着看，能看清两条路各自丢掉了什么。
- [Apple Code Signing Guide](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Introduction/Introduction.html)：理解为什么改动 `Info.plist`、资源或 Mach-O 后必须重新签名，以及签名身份和完整性如何绑定。
- [App Sandbox Design Guide](https://developer.apple.com/library/archive/documentation/Security/Conceptual/AppSandboxDesignGuide/AboutAppSandbox/AboutAppSandbox.html)：补齐 Sandbox、entitlements 与最小权限模型。读完再回看 `strip_sandbox`，其代价会更清楚。
- [Electron app.getPath](https://www.electronjs.org/docs/latest/api/app#appgetpathname)：理解 Electron 应用的数据目录从哪里来，也能看出命令行参数之外还有哪些适配入口。
- [Chromium user data directory](https://chromium.googlesource.com/chromium/src/%2B/HEAD/docs/user_data_dir.md)：查看 Chromium 对 user data dir、profile 和平台默认路径的官方说明。
