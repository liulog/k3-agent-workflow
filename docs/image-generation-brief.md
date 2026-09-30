# Image 生成说明：User、Main Agent 与 Build/Test Agent 的协作

> 用途：把下方“可直接复制的英文 Prompt”交给 Image 模型，生成一张准确表达当前 K3 Agent Workflow 的架构图。图中展示的是**明确授权的一次真实 Linux Build → K3 Test 流程**，不是默认 demo，也不是三个 Agent 互相聊天。

## 先理解真实关系

- **User**提出目标、边界和是否允许真实构建/板测；没有明确授权，不能进入真实硬件路径。
- **Main Agent**是 Pi RPC 子进程，负责审核候选、现有 `.config` 和 skills，提交一次任务，并在完成后核验结果、向 User 总结。Main 扩展是 Main Pi 进程内部的工具层，不是第四个 Agent。
- **Node.js Coordinator**是确定性控制器，不是模型。它托管本机 HTTP API、Engine、SQLite 状态和事件流；决定何时启动 Build/Test、校验结果并发送完成事件。
- **Build Agent**与 **Test Agent**是两个独立、短生命周期的 Pi 子进程，都使用 Luna。它们分别审核自己的输入、请求本阶段固定执行器、总结本阶段结果；不互相发消息、不共享对话上下文。
- 真正执行 `make Image` 和 SSH/板测的是受限的 **固定 Python executor**，不是 Agent 生成 shell 命令。Test executor 使用本机私有 `k3-auto` 配置连接远端 K3 板卡；凭据不传给 Test Agent。
- 一个实验严格串行：**Build → Coordinator 验证 Image → Test → Coordinator 验证证据**。全局最多一个活动 Worker，不画并行阶段。
- Pi Agent 是本机进程；只有固定 Test executor 通过 SSH 操作远端板卡。Coordinator 把经验证的结果经 SSE 通知启动器，再让同一个 Main Pi 会话核验并总结。
- Main Pi 进程在单轮任务期间存活；任务结束后进程退出，但固定 session 文件保留上下文。下一轮新 Main Pi 进程恢复这个 session。Build/Test session 不持久。

## 图面构图建议

- 横向 16:9、清晰的左→右主流程，画布可用 1920×1080。
- 用一个明显的大边框标出 **LOCAL LINUX HOST**；User 在主机边框外左侧；远端 **K3 DEVELOPMENT BOARD** 在主机边框外右侧。
- 主机内部上层放 Main Pi Agent 与嵌入其中的 Main Extension；旁边/下方放 Node.js Coordinator（HTTP API、Engine、SQLite、SSE）。另外画一个小型 **Persistent Pi Session** 存储，虚线连到 Main，标注“next run resumes same session”。
- 主机内部下层按顺序画两个编号阶段：`1 BUILD` 和 `2 TEST`。每个阶段都明确显示一个短生命周期 Luna Pi Worker 和它请求的固定 Python executor；它们合起来占用同一个 **ONE ACTIVE WORKER SLOT**。用 `Build verified first` 标注 Build → Test 的门槛。
- Linux source / existing `.config` 文件夹连接 Fixed Build Executor；`k3-auto skills` 卡片只连到 Test Agent（只读技能）；**private k3-auto config** 只连到 Fixed Test Executor，并以锁图标注明“never sent to Agent”。Fixed Test Executor 再通过标有 `SSH / TFTP` 的边界箭头连接远端板卡。
- 上方画用户任务进入 Main；中间画 Main Extension 经认证 HTTP POST 提交到 Coordinator；Coordinator 用 Pi RPC JSONL 派发阶段任务；阶段请求固定执行器；结果文件/哈希回到 Coordinator；底部用 SSE 终态通知返回 Main，再由 Main 向 User 报告。

## 箭头必须表达的顺序

1. `User → Main Agent`：goal + constraints + explicit authorization。
2. `Main Extension → Coordinator`：authenticated local HTTP `POST`，返回 `202 + experiment ID`（只表示任务已登记）。
3. `Coordinator → Build Luna Pi Worker`：启动隔离 Pi RPC 子进程，发送源码/配置快照和约束。
4. `Build Agent → Coordinator`：`stage_request_execution` 请求（不是构建成功）；Coordinator 核验后启动 fixed build executor。
5. `Fixed Build Executor → Coordinator`：Image + SHA-256 + Build ID + `result.json`；Coordinator 验证通过后才允许下一步。
6. `Coordinator → Test Luna Pi Worker`：传已验证 Image 身份、Build 报告及 k3-auto skills 快照。
7. `Test Agent → Coordinator`：`stage_request_execution` 请求；Coordinator 核验后启动 fixed test executor。
8. `Fixed Test Executor ↔ K3 Board`：SSH/TFTP、一次受控启动和 UnixBench；返回原始证据、状态与 cleanup 信息。
9. `Coordinator`：验证成绩、身份、异常扫描和清理，写 SQLite 并生成 terminal event。
10. `Coordinator / SSE watcher → Main session → User`：通知 Main；Main 用结果工具核验，再给 User 总结。

## 可直接复制给 Image 模型的 Prompt

```text
Create a polished, technically precise architecture infographic for “K3 Agent Workflow — Authorized Real Build → Board Test”. Use a 16:9 landscape canvas, clean flat vector shapes, strong contrast, ample whitespace, crisp orthogonal arrows, and a restrained professional palette: navy for the user/host boundary, violet for the Main Agent, blue for the coordinator, teal for Pi worker agents, orange for fixed executors, and dark green for the remote board. Avoid photorealism, 3D effects, decorative gradients, tiny text, and unnecessary icons.

The diagram must be factually exact:
- There are three logical Pi Agent roles: Main, Build, Test. They are OS processes, not three threads. Do not draw an Agent group chat.
- Main, Build, Test Pi processes run on the local Linux host. Build and Test are separate short-lived Pi RPC child processes and never run concurrently. There is exactly one global active Worker slot: Build completes and is validated before Test starts.
- The Node.js Coordinator is deterministic software, not an Agent or LLM. In this real one-shot launcher it hosts the local HTTP API, Engine, SQLite state, and SSE event watcher in the same controller process. Do not draw it as a fourth Agent or as a separate Pi process.
- Main Extension lives inside the Main Pi process. User talks to Main. Main submits a task through the extension to the Coordinator using authenticated local HTTP POST; HTTP 202 means “queued”, not success.
- Coordinator dispatches Build Luna through Pi RPC JSONL. Build Luna reviews a snapshot and requests a fixed Python Build executor. The executor, not the model, runs the fixed make Image operation against the Linux source tree and existing .config. It returns Image, SHA-256, Build ID, and a structured report to the Coordinator.
- Only after Coordinator validation does it start Test Luna, another isolated Pi RPC child. Test Luna receives the verified Image identity and read-only k3-auto skill snapshots, then requests a fixed Python Test executor. The model cannot run arbitrary shell or control hardware directly.
- The fixed Test executor reads private k3-auto configuration locally (draw a lock; label “credentials never sent to Agent”), then uses SSH/TFTP to reach a separate remote K3 development board for one controlled UnixBench run. It returns evidence and cleanup status to the Coordinator.
- Coordinator validates the full evidence, persists state/events in SQLite, and emits a terminal SSE event. The host watcher sends a final prompt to the same Main Pi session; Main uses a result tool to verify evidence and reports back to User.
- Main Pi process exits after each experiment, but a persistent Pi session file in local .workflow storage lets the next Main process resume the same conversation. Draw a small session-storage icon connected by a dashed line to Main, labeled “resume next run”. Build/Test contexts are ephemeral and isolated.
- There is no direct Main-to-board arrow, no direct Build-to-Test arrow, no shared agent transcript, and no parallel Build/Test lane.

Layout:
1) Put User outside the large “LOCAL LINUX HOST” boundary on the far left.
2) Inside the host, place Main Pi Agent + embedded Main Extension in the upper-left; place the Node.js Coordinator prominently in the center.
3) In the lower center, show two numbered sequential stages: “1 BUILD — Luna Pi Worker → Fixed Python Build Executor” then “2 TEST — Luna Pi Worker + k3-auto skills → Fixed Python Test Executor”. Add a clear “one active Worker slot” badge and a validation gate between the stages.
4) Put the Linux source + existing .config beside the Build executor, and private k3-auto config behind a lock beside the Test executor.
5) Put “REMOTE K3 DEVELOPMENT BOARD” outside the host boundary on the far right, connected only to the fixed Test executor by an SSH/TFTP arrow.
6) Route the verified result/SSE return along the bottom back to Main, then Main back to User.

Use only these exact short English labels so they remain legible: “User”, “Main Agent — Pi / Sol / medium”, “Main Extension”, “Node.js Coordinator”, “HTTP POST / 202 queued”, “SQLite state”, “SSE completion event”, “Persistent Pi Session”, “Build Luna — Pi RPC”, “Fixed Python Build Executor”, “Linux source + existing .config”, “Image + SHA-256 + Build ID”, “Validation gate”, “Test Luna — Pi RPC”, “Read-only k3-auto skills”, “Fixed Python Test Executor”, “Private config — never sent to Agent”, “SSH / TFTP”, “Remote K3 Board”, “UnixBench evidence + cleanup”, “ONE ACTIVE WORKER SLOT”, “BUILD → VERIFY → TEST”.

Add a small footer: “Demo and plan-only paths do not build or access hardware. Real execution requires explicit authorization.” Do not invent components, protocols, parallelism, extra agents, or success signals. Make the direction of every arrow unambiguous.
```

## 出图后人工核对

- Main Agent 与 Coordinator 是两个不同组件；Coordinator 不是 Agent。
- Main Extension 位于 Main Pi 进程内，不画成新的 Agent。
- Build/Test 是短生命周期、顺序运行的两个 Pi 子进程；没有 Agent-to-Agent 聊天。
- 真正执行命令的是固定 executor；远端板卡只与 Test executor 通信。
- 主线必须是 HTTP 提交 → Build → 验证 → Test → SQLite/SSE → Main 核验 → User。
- `202`、Agent 请求执行、Pi 轮次结束都不能单独画成实验成功。
