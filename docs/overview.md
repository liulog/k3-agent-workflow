# Workflow 与 Agent 协作关系

[返回 README](../README.md)

## 一句话概括

```text
User → Main Agent → 本机 workflow coordinator → Build Agent → 固定构建器
                                               → Test Agent  → 固定板测器 → k3 开发板
     ← 结果摘要 ←────────────── 已验证结果 ← SQLite / SSE ←──────────────┘
```

Agent 之间**没有直接聊天或共享会话**。Main 把任务交给 coordinator；coordinator 保存任务、按 Build → Test 顺序启动阶段；Worker 只审核阶段输入并请求固定执行器。编排器验证执行结果后，再把终态通知 Main。

## 角色与进程边界

| 角色/组件 | 是什么 | 职责与边界 |
|---|---|---|
| User | 人 | 提出目标并明确授权真实构建/板测；查看进展和最终证据。 |
| Main Agent | Pi RPC 子进程，`gpt-6.1-sol / high` | 检查候选、配置和 skills，提交唯一任务；完成后读取权威结果并总结。不能直接运行 shell 或控制板卡。优化设计约束由冻结版 `Documentation/DESING.md` 附加系统提示传入。 |
| Main 扩展 | Main Pi 进程内的扩展 | 暴露有限 workflow 工具；提交/查询使用本机 HTTP 和 Bearer token。它不是另一个 Agent。 |
| Coordinator | Node.js 进程中的 HTTP 服务、Engine 和事件监听器 | SQLite 持久化、FIFO 调度、Build→Test 状态机、结果验证、SSE 事件。真实入口中它与启动器同属一个 Node 进程，不是额外的 Pi Agent。 |
| Build Agent | 按阶段启动的 Pi RPC 子进程，`gpt-6-luna / medium` | 审核源快照、`.config` 和约束；只能请求一次固定 Build 执行，不拥有任意 shell 工具。阶段结束后退出。 |
| Fixed Build executor | Coordinator 启动的 Python 子进程 | 使用既有配置运行固定的 `make Image`，检查配置/源码是否变化，保存 Image、哈希和 Build ID。模型不生成执行命令。 |
| Test Agent | Build 完成后才启动的独立 Pi RPC 子进程，`gpt-6-luna / medium` | 读取构建身份及 `k3-auto` skills，审核后请求一次固定 Test 执行；不与 Build 共享 Pi 会话。 |
| Fixed Test executor | Python/SSH/串口执行链 | 检查占用和串口、上传并核对 Image；启动后只在 K3 板端分别按各 policy 的 performance 默认频率锁定并验证 CPU 频率、运行指定 RUN_ID、恢复板端原频率，采集 UnixBench 完整证据并核实清理。跳板机仅编排/转发，不改跳板机 CPU 频率；Pi Agent 本身不在板上运行。 |

“Main、Build、Test”是**三个逻辑 Agent 角色**，不是三个常驻线程。每轮真实任务期间，Node coordinator、Main Pi 子进程和当前阶段 Pi 子进程分别运行；固定执行时还会启动 Python、`make` 或 SSH 相关进程。Build 与 Test 共用一个活动 Worker 槽，绝不并行。

Main 的 **Pi 进程每轮任务结束会退出**，但它使用固定 Pi session ID 和本地 session 文件，因此下一轮新进程可恢复此前对话。Build/Test 使用一次性 session，不跨阶段共享上下文。

调度器在新任务、恢复 workflow、阶段执行结束时立即唤醒；另有 **10 秒空闲兜底扫描**，活动 Worker 执行期间不轮询队列。SSE 对 SQLite 事件的兜底检查间隔也是 10 秒，因此最坏会给完成通知增加约 10 秒延迟；每 5 秒的 SSE heartbeat 只保活连接，不检查任务或板卡。Main Agent 本身不轮询。

## 任务如何派发与回传

1. **User → Main**：描述目标、约束和执行授权。真实硬件操作必须由 User 明确授权，单纯 plan-only 不构成授权。
2. **Main 审核**：读取受限候选、配置摘要和测试 skills；记录是否接受候选。Main 不直接改内核或生成 shell 命令。
3. **Main → Coordinator**：扩展通过带本机 token 的 HTTP `POST /workflows/:name/experiments` 提交任务。HTTP `202` 和实验 ID 只表示已登记，不代表开始、构建成功或板测成功。
4. **Coordinator → Build Agent**：持久化任务后启动 Build Pi 子进程，通过 Pi RPC JSONL 传递本阶段快照。Agent 调用 `stage_request_execution` 仅是请求；Coordinator 验证 nonce/实验/阶段身份后，才启动固定 Python 构建器。
5. **Build → Coordinator**：构建器写入 `Image` 和 `result.json`。Coordinator 核验退出结果、配置/源码身份、产物哈希和 Build ID。失败则终止实验，不派 Test。
6. **Coordinator → Test Agent**：Build 成功后，另起 Test Pi 子进程；输入包含已登记 Image 身份、构建报告和 `k3-auto` skills。Build 的聊天记录不传给 Test。
7. **Test → 固定板测器**：Test Agent 审核后请求固定执行器。执行器做独占/串口预检，按固定步骤 TFTP、启动 K3、在板端锁定并验证 performance 默认频率、运行一轮 UnixBench、恢复频率、采集原始结果，并检查关机及串口释放。跳板机 CPU 不改动。预检冲突、频率锁定失败或状态不明时停止，不抢占、不盲目重试。
8. **Coordinator → Main**：Coordinator 根据 `status`、退出码、1/16-copy 成绩、异常、原始结果、Image 身份和清理证据决定终态，将状态写入 SQLite 并发布带事件 ID 的 SSE。启动器收到 SSE 后，在**同一个 Main Pi 会话**中调用结果工具并总结；Main 不靠轮询猜测进度。
9. **Main → User**：报告完成到哪一步、成绩、失败/未验证项和本地证据位置。只有完整证据全部通过才能报告真实成功；无基线不能声称性能提升。

## 数据与隔离

- Coordinator 在本机运行；默认服务监听 loopback。实验状态和有序事件写入 SQLite，阶段快照、日志、镜像及 JSON 证据写入 gitignored 的 `.workflow/`。
- Main ↔ Coordinator：本机 HTTP/JSON + Bearer token，进度由本机 SSE 交给启动器。
- Coordinator ↔ Pi Workers：Pi RPC 的 stdin/stdout JSONL；Main、Build、Test 没有 Agent-to-Agent 通道。
- 阶段交接：Coordinator 传本地文件路径、SHA-256、结构化报告和只读快照，不传完整聊天历史。
- 真实 Build/Test Agent 只持有受限的审核/请求/结果工具。凭据只由固定执行器运行时从 `k3-auto` 本地配置读取，不发给模型。
- 默认 demo 和 `linux-k3-plan` 与真实执行分离；只有 `node scripts/real-experiment.ts --execute` 进入真实的一次性流程。详见[真实执行说明](real-run.md)。

## 不能混为一谈的完成信号

```text
HTTP 202（任务已登记）
≠ Agent 请求执行（Coordinator 尚未核验结果）
≠ 固定脚本退出 0
≠ Coordinator 验证通过的实验成功
```

完整状态、通信协议和限制见[设计说明](design.md)、[通信协议](communication.md)及[真实实验记录](verification.md)。
