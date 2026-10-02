# 使用与运行维护

[返回 README](../README.md)

以下命令均在仓库根目录执行（另有说明除外）。本文说明默认 demo、主 Pi 接入、命令和故障恢复；明确授权的真实构建/板测使用[独立入口](real-run.md)，不能通过本页的 demo 命令执行；Linux → k3-auto 的配置与预演请看 [只读预演指南](plan-only.md)。

## 1. 零安装跑 demo

要求 **Node.js 24+**。使用 Node 自带 TypeScript 类型擦除、SQLite、测试运行器，不需要 npm install、tsx、tsc 或构建步骤。

```bash
cd ~/workspace/k3-agent-workflow
node scripts/demo.ts
node --test tests/*.test.ts
```

Demo 会创建临时后台服务，提交候选，通过 SSE 等待结果，验证重复提交返回同一实验，最后清理临时目录。不会启动 Pi、调用模型、编译或访问硬件。

典型输出：

```text
Submitted exp-...; response returned before execution.
1: experiment.queued
2: build.started
3: build.succeeded
4: test.started
5: experiment.succeeded
PASS: submit → simulated build → simulated test → SSE completion ...
```

另外可以检查扩展入口与**本机已安装 Pi** 的实际导出是否兼容：

```bash
node scripts/check-extension.ts
```

此检查只加载扩展并注册工具/schema，不启动 Agent 会话、不调用模型。若 Pi 不在 PATH，设置 `PI_CLI_PATH=/absolute/path/to/pi`。

## 2. 在 Pi 中使用

### 终端 A：启动后台服务

```bash
cd ~/workspace/k3-agent-workflow
node src/cli.ts serve
```

默认地址 `http://127.0.0.1:43127`，持久化目录 `.workflow/`，认证令牌自动生成于 `.workflow/token`，权限为 `0600`。程序不会打印令牌内容。

自定义端口或状态目录：

```bash
node src/cli.ts serve --port 43127 --state /absolute/path/to/workflow-state
```

### 终端 B：加载主控扩展

在你要阅读/修改汇编的项目目录里启动 Pi：

```bash
export K3_WORKFLOW_URL=http://127.0.0.1:43127
export K3_WORKFLOW_TOKEN_FILE="$HOME/workspace/k3-agent-workflow/.workflow/token"

pi --model your-provider/astra \
  --extension "$HOME/workspace/k3-agent-workflow/extension/index.ts"
```

`your-provider/astra` 是占位符，请替换成 Pi 中已配置可用的模型。扩展由 Pi 加载，无需安装到全局目录；本项目不会修改你的 `~/.pi` 配置。

在 Pi 内执行：

```text
/workflow attach asm-demo
```

等待状态栏显示 `connected`，然后对主 Agent 说：

```text
请调用 workflow_submit 提交一次模拟实验：
key 使用 candidate-001，candidate 使用 "ret\n"，
hypothesis 为“验证异步链路，不评估真实性能”。
提交后停止查询，等待 workflow 完成消息。
```

默认完成消息会进入会话，但**不会自动调用模型继续推理**。如需自动闭环，由你显式开启：

```text
/workflow auto on
```

再告诉主 Agent：“收到结果后分析；本次只尝试 2 个候选，不将模拟数字当作优化收益”。编排器另有每 workflow 5 个实验的硬上限，包括失败和取消；重复提交不占新额度。自动模式可能消耗模型 token。

### 常用命令

| 命令 | 行为 |
|---|---|
| `/workflow attach NAME` | 绑定/重连一个 workflow，自动续轮重置为 OFF |
| `/workflow status` | 显示任务状态 |
| `/workflow auto on` / `off` | 控制完成通知是否触发主 Agent 续轮 |
| `/workflow pause` | 停止后续派发，并关闭自动续轮；当前任务继续 |
| `/workflow resume` | 恢复派发，不自动打开续轮 |
| `/workflow cancel EXP_ID` | 请求取消，等待运行中的 Worker 真正退出 |
| `/workflow detach` | 断开主会话，后台任务继续 |

模型工具：`workflow_submit`（原模拟任务）、`workflow_plan_linux`（Linux→k3-auto 只读预演）、`workflow_status`、`workflow_result`、`workflow_cancel`。

关闭 Pi 不会关闭后台服务；重新进入会话后显式 attach，扩展会按该会话分支中的游标补收事件。切换会话/分支会停止旧订阅。一个 workflow 同时只允许一个事件订阅者；另一个主会话想接管时，先 detach 旧会话。

**暂停不会撤销已经排入 Pi 的 follow-up，也不能终止已经开始的模型推理。** 必要时同时在 Pi 中停止当前运行。异步提交一旦服务端接收，取消工具调用本身也不会撤销后台任务，需调用 `workflow_cancel`。

## 3. 可选：使用真正的 Pi 子进程（仍然是模拟任务）

先关闭原后台服务，再运行：

```bash
node src/cli.ts serve --rpc-demo-model your-provider/luna
```

前提：Pi 在 PATH，Luna 模型已配置认证。主会话仍使用 Astra，build/test 阶段分别使用 Luna。Worker 每任务新建进程，避免跨候选上下文污染。

此模式：

- 给子进程发送真实 Pi RPC prompt；记录 JSONL 与 stderr；
- 仅开放 `read,write`，禁用发现的扩展、技能、模板和项目受信资源；
- 保留正常的全局上下文规则；不向 Worker 传递主控 token 环境变量；
- 要求生成模拟 artifact 和严格的 `result.json`，不提供 bash 工具；
- 60 秒超时、输出大小限制、进程退出检测、取消与进程组清理。

本节的原 `demo` 模式通过假 Pi 子进程做协议回归。新增 `linux-k3-plan` 模式另已完成真实 Luna 只读预演，见 [实验报告](experiments/linux-k3-plan-smoke.md)。两者都不代表已经完成真实编译/板测集成。

## 数据、日志与恢复

```text
.workflow/                    # gitignored，不提交令牌/实验数据
├── token
├── daemon.lock               # 单后台进程保护，记录 PID
├── state.sqlite              # SQLite WAL：任务、事件、workflow
└── runs/exp-.../
    ├── candidate.s           # 提交时固化的字节，SHA-256 绑定
    ├── manifest.json         # 初始请求清单；当前状态以 SQLite 为准
    ├── build/
    │   ├── artifact.txt      # 非可执行的模拟产物
    │   ├── result.json
    │   └── worker.log        # 默认模拟模式
    └── test/
        └── result.json
```

RPC 模式在各阶段目录增加 `rpc.jsonl` 和 `stderr.log`。通过 `workflow_result` 得到目录、产物哈希、结构化结果与错误。

- 重复提交必须使用相同 key 和完全相同 payload，否则返回 409。
- 构建失败不会派发 test；产物哈希、数据格式、正确性检查失败不能算成功。
- 正常关停会取消当前模拟任务。异常崩溃留下的 `running` 任务在重启时转为 `needs_attention`，**不自动重试**；排队任务可恢复。
- 崩溃可能留下 `daemon.lock`：先检查记录的 PID、确认旧 Worker 均已退出，再手动删除陈旧锁并重启。不能仅因连接断开就启动第二个调度器。
- 完成消息只有进入 Pi transcript 后才确认持久化投递游标。服务端状态和 Pi transcript 无法跨系统原子提交，仍可能重放；恢复后按实验 ID 查询事实，重复提交使用原 key。

## k3-auto 与真实 Test 阶段

[`integrations/k3-auto`](../integrations/k3-auto) 固定引用 [liulog/k3-auto](https://github.com/liulog/k3-auto)。默认 demo 不加载它；`linux-k3-plan` 只把三个 skills 作为只读资料提供给 Test Luna。

真实 one-shot 中，Test Luna 阅读 `k3-benchmark`、`k3-lab`、`k3-status` skills 并审核固定步骤；它不能运行 skill 命令或 shell。实际连接开发板的是受限 Python executor：运行时读取 `../k3-auto/config` 的私有配置，通过固定流程调用 `k3ctl`、SSH、TFTP 和板测 runner。私有配置和凭据不进入 Agent 输入。边界见[真实执行说明](real-run.md)。

submodule 固定在已登记提交。未来更新版本时，先审阅上游变更，再单独提交主仓库的 submodule 指针；不自动跟随上游最新版本。

首次克隆时可同时获取：

```bash
git clone --recurse-submodules git@github.com:liulog/k3-agent-workflow.git
```

已有克隆可获取主仓库锁定的版本：

```bash
git submodule update --init --recursive
```

运行原 demo 不需要初始化此 submodule；真实资料的 plan-only 预演需要初始化它。未来更新其版本时，应审阅上游变更，再单独提交主仓库中的 submodule 指针，不自动跟随上游最新提交。

## 当前边界

这不是生产级硬件控制系统，也不是 OS 安全沙箱：原 demo Worker 的 `read,write` 仍有该用户的文件权限；plan-only 另以受限 `read` 工具强制执行资料白名单，但不隔离恶意同用户进程或 Pi runtime。HTTP token 防止无授权的本机请求，不代替操作系统隔离。

Coordinator 当前不支持：通用构建配置、完整 Git 仓库快照、多板资源租约、远程 Worker、通用自动重试、产物 GC、预算在线修改、成本/时间总预算、跨机器认证。独立串行 matrix 已有显式授权的一次通信恢复与五分钟网络等待，不改变默认 profile 的安全边界，见[可靠性与重试](reliability.md)。

当前真实入口只支持固定工作树、既有 `.config`、单 Image 和单板 UnixBench；不支持通用构建配置、完整可复现 worktree、多板租约或自动恢复。扩展这些能力前仍需补齐 commit/worktree 快照、部署版本确认、跨工具设备独占和故障接管；不能把 demo 的“杀进程即取消”用于硬件任务。
