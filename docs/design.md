# 设计与协议

Agent 分工、进程边界和逐条消息示例见 [通信协议说明](communication.md)；图示见 [协作图](architecture.svg) / [时序图](sequence.svg)。

`demo` 和 `linux-k3-plan` 默认不执行构建或板测；另有显式授权的 `linux-k3-real` one-shot profile，采用固定 executor。只读预演见 [plan-only](plan-only.md)，真实边界见 [real-run](real-run.md)。下文 `simulated` 结果格式描述的是 `demo` profile。

## 决策记录

1. 标准 `serve` 路径使用独立后台服务；opt-in one-shot 真实入口则在同一 Node 进程中托管 coordinator、HTTP/SSE、启动器与任务监听。主控扩展不直接持有 Build/Test executor。
2. TypeScript 源码由 Node 24 直接运行；用 `node:sqlite` 避免引入 SQLite native addon 的安装/编译流程。
3. 固定 build→test 状态机，不引入通用 DAG 或消息队列。
4. 全局只有一个活动 Worker。按实验提交顺序执行 build→test，不并行不同实验或不同阶段；暂停的 workflow 跳过，失败后继续处理后续实验。取消须等 Worker 真正退出才释放槽位。
5. 默认 demo/plan Worker 使用原生 JSONL adapter，避免运行时依赖并便于 fake Pi 测试；真实 Main 与 Build/Test 使用本机 Pi `RpcClient`，均遵循 Pi RPC 文档。
6. Build/Test 每阶段新 Pi 进程和隔离上下文；真实 Main 进程每轮结束，但通过固定 Pi session ID 持久复用上下文。
7. demo candidate 是提交时固化的文本；真实执行对必要源码、配置和技能建立受限快照及哈希，不是完整、可复现的 worktree。

### MVP 收敛原则

只解决本机 Build→Test→通知闭环，不扩展通用平台。已有 HTTP/JSON、RPC、SSE 接口保持兼容；demo 留作无模型验收基线。三个 profile 共用状态机，只有结果契约和真实执行权限不同；同一结果文件只读取一次用于解析和登记哈希。

SQLite/事件补收防止断线丢结果；幂等避免重提重复执行；只读白名单和校验防止误执行、虚假成功。这些是可靠性底线，不以删行数为由去掉。多板、多机、动态 DAG、并行流水线不属于当前 MVP。旧 SVG 为拓扑参考，其“阶段各一槽”调度描述已由全局单槽替代。

## 状态机

```text
submit
  └─ queued(build) → running(build) → queued(test) → running(test) → succeeded
        │                 │                │              │
        └─ cancelled      ├─ failed        └─ cancelled   ├─ failed
                          ├─ cancelled                   └─ cancelled
                          └─ needs_attention（重启恢复）
```

真实 Test 属于有副作用的硬件阶段，运行中断、证据不完整或关闭状态不明均转为 `needs_attention`，本机服务退出时等待其清理，不盲目杀 SSH。普通模拟 Worker 的取消以 Worker 完成退出为界。暂停仅阻止新派发，不杀任务。

SQLite 中任务变更与对应事件写入同一事务。提交先写源码快照和初始 manifest，再提交数据库并响应；中途失败可能留下无引用的实验目录，但不会返回未持久化的实验 ID。

源码/产物使用 SHA-256，每阶段执行前后检查源码，测试前后检查构建产物。结果必须为普通文件，不接受符号链接；普通 JSON 输出限制 1 MiB，真实 Image 单独放宽到 512 MiB。

## HTTP API

所有接口要求 `Authorization: Bearer TOKEN`。仅绑定 IPv4 loopback，拒绝带 Origin 的浏览器请求，无 CORS。请求体最多 100 KB，日志/任务数据可能包含源码，不应对公网开放。

| Method | Path | 描述 |
|---|---|---|
| GET | `/health` | 返回当前启用的 profile；真实 profile 只在 opt-in one-shot 服务中开放 |
| GET | `/workflows/:name` | workflow 状态和实验列表 |
| POST | `/workflows/:name/experiments` | 幂等提交，返回 202 和实验 |
| GET | `/workflows/:name/experiments/:id` | 获取实验 |
| POST | `/workflows/:name/cancel/:id` | 请求取消 |
| POST | `/workflows/:name/pause` | 停止新派发 |
| POST | `/workflows/:name/resume` | 恢复新派发 |
| GET | `/workflows/:name/events?after=N` | SSE，重放 ID>N 的事件 |

提交 body：

```json
{
  "key": "candidate-001",
  "candidate": "ret\n",
  "hypothesis": "Verify asynchronous orchestration",
  "profile": "demo"
}
```

同 workflow 的 key 唯一；key 重用但内容不同返回 409。计数上限固定为 5，创建新 workflow 由用户显式 attach，模型工具不提供创建其他 workflow 的入口。

订阅者提供 `x-workflow-owner` 随机 ID。一个 workflow 只允许一条 SSE 流；连接存活期间，修改请求必须携带相同 owner。连接断开后释放绑定。**这是单会话协调，不是多租户权限系统**：持有 bearer token 的本机程序仍是受信客户端。

事件：

```json
{
  "id": 5,
  "workflow": "asm-demo",
  "type": "experiment.succeeded",
  "experimentId": "exp-...",
  "data": {
    "status": "succeeded",
    "stage": "test",
    "result": { "simulated": true, "correctness": true, "samples": [100, 101, 99], "unit": "synthetic-cycles" }
  }
}
```

事件 ID 全数据库单调递增，某个 workflow 的 ID 可以有间隙。SSE 每批至多 100 条，心跳 5 秒，客户端 15 秒无数据则重连，收到完整事件并调用回调后更新内存游标。慢消费者限制缓冲区，不要求模型理解心跳。

## 常驻扩展模式中的 Pi 主会话集成

- 工具提交立即返回，不把长任务当作等待中的 Pi tool call。
- 扩展生命周期中启动/关闭 SSE；factory 只注册工具和事件。
- 完成事件通过 `pi.sendMessage` 送入 transcript。`deliverAs: followUp` 保证自动模式不抢占当前工作；`triggerTurn: false` 时仅显示/保存结果。
- 用 `message_end` 确认完成消息实际进入 transcript 后才推进可恢复游标。未确认消息会阻止后续进度事件把持久化游标越过它。
- 用 `pi.appendEntry` 保存分支相关绑定和游标。每次启动从当前分支读取，显式 attach 后重连；自动续轮开关不持久化。
- 一次事件可能在极端崩溃窗口被重放。不能提供跨 SQLite 与 Pi 会话的 exactly-once；业务提交通过 key 幂等，结果 ID 可复查。
- 恢复时先读取 workflow 状态；旧通知默认不自动启动模型。
- 以上为可交互扩展 attach 的通知方式；真实 one-shot 由宿主订阅 SSE，终态后通过同一个持久 Main Pi session 发起最终结果核验轮次。

## Worker 与结果协议

`Worker.run({ experiment, stage, directory, signal })` 必须结束全部自身工作后才 resolve/reject。普通返回不等于业务成功，Engine 独立校验磁盘结果。

### build/result.json

```json
{ "simulated": true, "kind": "build", "sourceHash": "<snapshot sha256>", "artifact": "artifact.txt" }
```

### test/result.json

```json
{
  "simulated": true,
  "kind": "test",
  "artifactHash": "<registered artifact sha256>",
  "correctness": true,
  "samples": [100, 101, 99, 100, 100],
  "unit": "synthetic-cycles"
}
```

指标样本数量 3..10000，每项有限且为正数。数字刻意固定：这是通信链路 demo，不是测量器，不应用于接受任何汇编优化。

RPC adapter 仅用 LF 切 JSONL，不使用可误拆 Unicode 分隔符的 readline。prompt 成功与 `agent_settled` 两个条件均满足后，最后一条 assistant message 必须以 `stop` 正常结束；`agent_end` 不作为完成信号。错误/退出/超时导致失败，取消导致 cancelled。日志合计限制 16 MiB，单条未拆分记录限制约 2 MiB。

在 POSIX 系统上创建独立进程组，结束时发送 SIGTERM，必要时升级 SIGKILL，并等待 stdio 关闭才释放槽位。它适合当前的无硬件模拟子进程，**不提供刷写安全性或容器隔离**。

## 已实现的真实执行与生产缺口

真实 one-shot 采用明确授权、固定 Build/Test executor、现有配置保护、源码/镜像身份校验、远端协作锁、串口/runner 预检、完整证据门禁和保守失败处理。它已在单板、单配置完成一次实测；详见 [实验报告](experiments/linux-k3-csrrsi-fast-real.md)。

这仍不是生产级硬件控制系统：源码身份不是完整可复现 worktree；远端锁无法约束不遵守它的工具；掉电/网络/主机故障后仍需人工核实；没有自动恢复/重试、多板资源管理、独立正确性基准、基线/候选交替测试或噪声判据；Pi 同用户进程也不是 OS 安全沙箱。因此一次成功实测不能替代生产部署审查或性能提升证明。
