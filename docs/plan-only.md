# linux-riscv-gate → k3-auto：只读预演 MVP

本阶段验证 **Build Luna 和 Test Luna 能否接收任务、阅读资料/skill、交接结构化计划、把结果回传**，不验证真实构建或硬件是否可用。

```text
主 Pi 的 workflow_plan_linux 工具（或单次实验脚本代替主 Agent）
  → HTTP 提交 profile=linux-k3-plan
  → 固化选定的构建文档及 k3-auto skill 快照
  → Build Luna：只读快照，最终回答编译计划 JSON
  → workflowd：记录 read 证据，校验并保存计划，计算 SHA-256
  → Test Luna：显式加载 k3-auto skills，阅读全部 skill 及 Build 计划
  → workflowd：校验 buildPlanHash，保存测试计划
  → SSE 完成通知 → 主会话查看两份计划与 blockers
```

这里的“build succeeded / experiment succeeded”表示对应**规划阶段完成**，不表示真正运行了编译或测试。返回值明确包含：

```json
{
  "mode": "plan-only",
  "planningCompleted": true,
  "buildExecuted": false,
  "boardAccessed": false
}
```

## 1. 配置两个角色

复制根目录 `planning.example.json` 为 `planning.local.json`，修改模型名称。后者已 gitignore，不提交本机配置。

```json
{
  "mode": "plan-only",
  "linuxRepo": "../linux-riscv-gate",
  "k3AutoRoot": "./integrations/k3-auto",
  "build": { "model": "openai-codex/gpt-5.6-luna" },
  "test": { "model": "openai-codex/gpt-5.6-luna" },
  "timeoutMs": 300000
}
```

路径相对于配置文件所在目录解析。Build/Test 是两个独立 Pi 进程，可以选择相同模型，也可以分别配置；模型必须已在本机 Pi 中可用并认证。以上模型在本机验证过，不保证其他环境可用。

配置只接受 `plan-only`，不能加 `execute: true` 或 `tools: bash` 来开启真实任务。

## 2. 三档验证，按需选择

### A. 静态检查：无模型、无硬件

```bash
node src/cli.ts check-plan --plan-config planning.local.json
```

检查必需构建文档、skill frontmatter、快照和哈希。只使用本地文件 API，不调用 git/编译器/k3ctl，也不检查模型网络可用性。

### B. 假 Pi 的真实管道实验：无模型、无硬件

```bash
node scripts/plan-experiment.ts --config planning.local.json
```

两个 Node 子进程模拟 Pi JSONL 协议，使用实际资料快照和与 Worker 相同的只读工具核心；假 Pi 计划和结果额外标记 `simulated: true`。验证 HTTP、RPC、SSE、计划交接及校验，但不能证明真实模型理解了任务。

### C. 真实 Luna 预演：调用模型，仍不执行任务

```bash
node scripts/plan-experiment.ts --config planning.local.json --real-models
```

`--real-models` 必须显式提供，会消耗模型 token。每个 Worker 默认最多 5 分钟；只提交一个实验，不自动重试整个实验，不进入自动优化循环。

脚本在临时端口启动 workflowd，以主控客户端代替 Astra 提交任务并等待 SSE 终态，最后关闭服务。没有调用主 Astra 模型，也没有完成交互式主 Pi 的真人操作验收。

每次实验保留 `.workflow/plan-experiment-<timestamp>/`。默认假 Pi 与真实 Luna 的模式会在控制台和 `run-summary.json` 中明确区分。

## 3. 后续从主 Agent 接入

现在准备好了接口，但本阶段不部署真实编译任务。

终端 A：

```bash
node src/cli.ts serve --plan-config planning.local.json
```

终端 B，在目标项目目录启动主 Pi：

```bash
export K3_WORKFLOW_URL=http://127.0.0.1:43127
export K3_WORKFLOW_TOKEN_FILE="$HOME/workspace/k3-agent-workflow/.workflow/token"
pi --model your-provider/astra \
  --extension "$HOME/workspace/k3-agent-workflow/extension/index.ts"
```

在主 Pi 内：

```text
/workflow attach linux-k3-plan-demo
```

等状态栏 connected 后，发送：

```text
这是一项只读预演，不授权执行编译或开发板操作。
请仅调用 workflow_plan_linux：
key = linux-k3-plan-001
benchmark = unixbench
task = 为 linux-riscv-gate 制定保留现有配置的 Image 编译计划，
       再交给使用 k3-auto skills 的 Test Luna 制定板测计划；未知项写入 blockers。
不要直接运行 bash/编译/SSH。提交后等待通知，再用 workflow_result 查看两份计划。
```

自动续轮默认关闭；需要结果到达后主 Agent 自动分析时，用户显式执行 `/workflow auto on`。这个开关**不改变 Worker 的只读权限**，也不会把计划升级为执行任务。

`workflow_plan_linux` 参数：

```json
{
  "key": "linux-k3-plan-001",
  "task": "编译 linux-riscv-gate 后交接给 k3-auto 的只读预演",
  "benchmark": "unixbench"
}
```

模型不能通过此工具指定任意原始路径、任意 skill、命令执行器或权限；源目录与模型由操作者的 daemon 配置指定。后端未开启 `--plan-config` 时返回 409，不偷偷退回 demo。

`workflow_result` 返回 `plans.build`、`plans.test`、计划哈希、证据目录、摘要与阻塞项。

## 4. 不靠提示词维持只读

### 输入快照

Build 读取：

- 必需：Linux `Makefile`、`scripts/build_kernel.sh`。
- 存在时附加：`README` / `README.md`、`scripts/iee-build-k3-cycles.sh`、`IEE_GATE_COMPARISON.md`、`arch/riscv/configs/k3_bianbu_defconfig`。

Test 读取：

- k3-auto 的 `README.md`、`AUTOLINK_K3_BENCHMARK_GUIDE.md`；
- `k3-benchmark`、`k3-lab`、`k3-status` 三个 `SKILL.md`；
- 前一阶段已登记的 Build 计划。

只复制明确列出的普通文件，拒绝越界/符号链接和超大文件，不复制 `.git`、`.config`、`config/jump.toml`、SSH 凭据或完整源树。文件 hash 随 manifest 保存，每阶段前后校验。

这是**规划资料快照**，不是可复现的 Linux 源码版本。实际工具链、当前 `.config`、板卡状态等有意不探测，必须报告为未知/待授权。

### Tool allowlist 与读取证据

Pi Worker 启动参数包含：

```text
--tools read
--no-extensions --extension <本项目 worker-extension/plan-only.ts>
--no-skills
```

Test 额外显式指定三个快照 skill：

```text
--skill <snapshot>/skills/k3-benchmark/SKILL.md
--skill <snapshot>/skills/k3-lab/SKILL.md
--skill <snapshot>/skills/k3-status/SKILL.md
```

`--no-skills` 禁用自动发现，不禁用显式 `--skill`。这些 skill 的操作说明仅作为未来计划参考，不能视作当前执行授权。

Worker 扩展覆盖 `read`：

- 仅允许指定快照与 Build 计划的精确路径；
- 核验普通文件、规范路径、SHA-256；
- 每次返回带哈希和行区间的 guard receipt；
- 分页后必须完整覆盖文件内容，不能只读一行就声称加载 skill；
- 不提供 bash、write、edit 或其他执行工具；也拒绝 `user_bash`。

后台检查 Pi 的实际 tool 事件，而不是相信模型填写 `skillsUsed` 就算读过。每阶段最多容忍 3 次失败的 read 供模型纠正拼写；失败读取不产生证据，越权成功/无 guard receipt 则拒绝结果。

这属于 Pi 工具级约束，不是 OS 沙箱。Pi 的认证/资源加载仍由受信 Pi runtime 执行；同用户进程或恶意扩展的隔离不在本 MVP 范围。

### 结果输出

Worker 不获得写文件工具，而是在最终 assistant 消息中返回 JSON。workflowd 验证身份、模式、固定 `commandsExecuted:false` / `boardAccessed:false`、必需字段，再保存文件。

- Build 的 `expectedImage` 只是未来期望路径，不是已存在产物。
- Test 的 `buildPlanHash` 必须匹配前一阶段计划文件。
- `steps[].command` 永远是字符串数据，不送入 shell。
- 禁止顶层 scores/samples 等未定义字段，不生成假 benchmark 数字。
- plans 的语义质量仍需人审：模型可能重复 Build 步骤、遗漏顺序细节、保留占位符；结构校验通过不意味着命令可以直接执行。

## 5. 实验证据

```text
.workflow/plan-experiment-.../
├── run-summary.json
├── state.sqlite
└── runs/exp-.../
    ├── task.json                    # 请求的固定字节；sourceHash 在此模式表示请求哈希
    ├── manifest.json                # 含资料快照路径与各文件 SHA-256
    ├── inputs/linux-riscv-gate/...
    ├── inputs/k3-auto/...
    ├── build/
    │   ├── launch.json              # 模型、tools、skill 参数，无认证环境
    │   ├── read-policy.json
    │   ├── prompt.txt
    │   ├── rpc.jsonl
    │   ├── stderr.log
    │   ├── assistant.txt
    │   ├── read-evidence.json
    │   └── result.json              # 编译计划，非 Image
    └── test/
        └── ...                     # 同样的证据文件 + 板测计划
```

第一次失败的日志也保留；不为了得到成功状态删改原实验。真实验证记录见 [MVP 实验报告](experiments/linux-k3-plan-smoke.md)。
