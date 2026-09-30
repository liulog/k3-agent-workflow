# Agent 协作与通信协议

本文覆盖默认 demo、只读 plan 和 opt-in 真实执行三条路径。组件角色关系见[架构说明](overview.md)，真实操作边界见[真实执行说明](real-run.md)。

## 1. 实际关系：控制器中介，不是 Agent 群聊

```text
User ↔ Main Pi Agent（Sol）+ Main 扩展
                    │ 本机 HTTP/JSON：提交实验
                    ▼
             Node Coordinator
             Engine + SQLite + SSE
                    │ Pi RPC JSONL：阶段快照
                    ├─ Build Pi Agent（Luna）→ 固定 Build executor
                    └─ Test Pi Agent（Luna） → 固定 Test executor → k3-auto / 板卡
                    │
                    └──────── SSE 终态 → Main 同一 Pi 会话查结果 → User
```

真实入口中，Node Coordinator 与 HTTP/SSE 服务由 `scripts/real-experiment.ts --execute` 同一 Node 进程托管。Main Agent 是它启动的 Pi 子进程；扩展在 Main Pi 进程内。Build/Test 按阶段依次启动为不同 Pi 子进程，分别只有审核、固定执行请求和读结果工具。它们不直接互发消息，也不共享聊天历史。实际编译、SSH 和板测由固定执行脚本执行，不是模型生成 shell。

Main Pi 每次真实任务结束会退出；固定 session ID 及 `.workflow/main-agent-session/` 使下一轮新 Pi 进程恢复同一 Main 对话。Build/Test 使用一次性会话。

## 2. Main 如何提交任务

Main 的受限扩展工具将一次真实任务提交为：

```http
POST /workflows/authorized-real-run/experiments
Authorization: Bearer <本机私有令牌>
Content-Type: application/json
```

```json
{"key":"authorized-existing-config-unixbench","profile":"linux-k3-real"}
```

Coordinator 写入 SQLite 并立即返回 HTTP `202` 和 experiment ID。它只表示“任务已登记”，不是 Build 已启动，更不是实验成功。幂等 key 防重复提交；真实 one-shot 服务仅允许一个真实实验。

Main 先通过工具读取候选快照、必需配置项和 `k3-auto` skills，再记录候选审查意见。Main 扩展不接受模型给出的 shell、镜像路径或任意执行命令。真实执行只能由显式启动 `--execute` 的宿主流程开放。

## 3. Build/Test 如何派发

Coordinator 是唯一调度方：一个全局活动 Worker，严格执行 `Build(A) → Test(A)`；不并行其他实验。

每个阶段由 `AgentWorker` 启动新的 Pi RPC 子进程，通过 stdin/stdout JSONL 发送阶段输入。输入含当前阶段、实验 ID、nonce、固定文件快照/哈希及边界。Pi `prompt accepted` 只表示 prompt 已接收，不表示工作完成。

Agent 必须先调用 `stage_instructions` 读完本阶段输入，再调用无参数的 `stage_request_execution`。该工具只写请求标记，不执行命令。Coordinator 验证 nonce、experiment ID 和阶段后，才启动固定 Python executor。模型没有 bash、任意路径或命令参数。

- **Build**：executor 只运行固定 `make Image`，使用既有 `.config`，不 clean/defconfig/install。它记录配置与源码身份，写入 Image、SHA-256、Build ID 和 `result.json`。任一身份检查失败都不会派 Test。
- **Test**：仅在 Build 产物校验成功后启动 Test Luna，并提供 Image 身份和 `k3-benchmark`、`k3-lab`、`k3-status` 快照。它请求固定 Test executor；后者读取 `k3-auto` 本地配置，检查 board/serial 占用，上传并核对 Image，运行唯一 RUN_ID，采集板端 UnixBench 证据并检查收尾。

Agent 的执行请求回执不是执行成功。Coordinator 要等固定 executor 结束，再验证结构化结果、证据和产物哈希；不使用 Agent 的自然语言“成功”替代机器可判定证据。

## 4. 结果如何回到 Main/User

Coordinator 把 Build/Test 报告放在 `.workflow/real-<timestamp>/runs/<id>/`，更新 SQLite 状态并发布带递增 event ID 的 SSE。真实入口的 Node 宿主订阅 SSE；终态到达后，它向同一个 Main Pi session 发送后续 prompt。Main 再用 `workflow_real_result` 查询实验记录和阶段 Agent 总结，向 User 汇报结果与证据位置。

```text
Build/Test result.json + artifacts
    → Coordinator validation
    → SQLite terminal status + SSE event
    → Main host listener
    → same Main Pi session: workflow_real_result
    → User-facing summary
```

SSE 事件是进度通知；权威结果仍是经 Coordinator 验证的任务记录与阶段证据。HTTP `202`、Agent 的 `stage_request_execution` 回执、Pi 的 `agent_settled`、固定脚本退出码都**不能单独等同实验成功**。

只有以下证据全部齐全才判定 UnixBench 成功：status、exit code、1-copy 与 16-copy 完整成绩、异常扫描、原始结果、板端 Image/config/Build ID 身份及 cleanup。中断、串口冲突或清理状态不明时标记 `needs_attention`，不自动重试。

## 5. 默认模式与权限边界

- `demo`：模拟结果，必须带 `simulated: true`；不启动模型、编译或访问板卡。
- `linux-k3-plan`：真实 Luna 只读规划；不运行计划命令、不连接硬件，结果标记 `mode: "plan-only"`。
- `linux-k3-real`：独立 one-shot 入口，需明确用户授权；固定构建/板测脚本，模型没有 shell 工具。

HTTP 绑定本机并要求 Bearer token；阶段状态、事件、快照和日志保存在 gitignored 的 `.workflow/`。硬件安全仍不是 OS 安全沙箱；远端锁也不能阻止不遵守锁的其他工具。SSH 断开不能证明板卡下电。

状态机、HTTP 路由和失败处理见[设计说明](design.md)；本轮真实证据摘要见[实验记录](experiments/linux-k3-csrrsi-fast-real.md)。
