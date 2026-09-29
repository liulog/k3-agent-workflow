# 设计与协议

Agent 分工、进程边界和逐条消息示例见 [通信协议说明](communication.md)；图示见 [协作图](architecture.svg) / [时序图](sequence.svg)。

新增 `linux-k3-plan` 是同一编排器中的独立只读 profile，交接 Build 计划而不是模拟 artifact；数据契约和硬边界见 [plan-only](plan-only.md)，真实模型验证见 [实验记录](experiments/linux-k3-plan-smoke.md)。下文原有 `simulated` 结果格式描述的是 `demo` profile。

## 决策记录

1. 使用独立后台进程，而不是由主控扩展启动/持有任务执行器。扩展关闭不影响任务。
2. TypeScript 源码由 Node 24 直接运行；用 `node:sqlite` 避免引入 SQLite native addon 的安装/编译流程。
3. 固定 build→test 状态机，不引入通用 DAG 或消息队列。
4. 每种阶段只有一个槽位，全工作流共享。build(N+1) 可与 test(N) 重叠；test 不并行。
5. RPC 采用约 150 行的原生 JSONL adapter，而非直接引入 SDK Runtime。这是与初步 RpcClient 建议的有意取舍：零运行依赖、可用 fake Pi 测试、直接控制进程组/超时/日志。遵循本机 Pi RPC 文档，适配器独立可替换。
6. Worker 每任务新进程，不复用常驻模型会话。启动开销换取上下文隔离。
7. demo candidate 是直接提交的单段汇编文本，不引用可变路径。真实仓库快照不在 MVP 范围。

## 状态机

```text
submit
  └─ queued(build) → running(build) → queued(test) → running(test) → succeeded
        │                 │                │              │
        └─ cancelled      ├─ failed        └─ cancelled   ├─ failed
                          ├─ cancelled                   └─ cancelled
                          └─ needs_attention（重启恢复）
```

test 阶段中断重启同样转为 `needs_attention`。当前正在执行的取消以 Worker 完成退出为界，状态在此前保持 running。暂停仅阻止新派发，不杀任务。

SQLite 中任务变更与对应事件写入同一事务。提交先写源码快照和初始 manifest，再提交数据库并响应；中途失败可能留下无引用的实验目录，但不会返回未持久化的实验 ID。

源码/产物使用 SHA-256，每阶段执行前后检查源码，测试前后检查构建产物。结果必须为普通文件，不接受符号链接，单输出文件限制 1 MiB。

## HTTP API

所有接口要求 `Authorization: Bearer TOKEN`。仅绑定 IPv4 loopback，拒绝带 Origin 的浏览器请求，无 CORS。请求体最多 100 KB，日志/任务数据可能包含源码，不应对公网开放。

| Method | Path | 描述 |
|---|---|---|
| GET | `/health` | 返回 simulation-only 模式 |
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

## Pi 主会话集成

- 工具提交立即返回，不把长任务当作等待中的 Pi tool call。
- 扩展生命周期中启动/关闭 SSE；factory 只注册工具和事件。
- 完成事件通过 `pi.sendMessage` 送入 transcript。`deliverAs: followUp` 保证自动模式不抢占当前工作；`triggerTurn: false` 时仅显示/保存结果。
- 用 `message_end` 确认完成消息实际进入 transcript 后才推进可恢复游标。未确认消息会阻止后续进度事件把持久化游标越过它。
- 用 `pi.appendEntry` 保存分支相关绑定和游标。每次启动从当前分支读取，显式 attach 后重连；自动续轮开关不持久化。
- 一次事件可能在极端崩溃窗口被重放。不能提供跨 SQLite 与 Pi 会话的 exactly-once；业务提交通过 key 幂等，结果 ID 可复查。
- 恢复时先读取 workflow 状态；旧通知默认不自动启动模型。

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

## 后续真实硬件接入门槛

不是把 `DemoWorker` 中的写文件替换成任意 bash 就算完成。至少需要：

1. 明确授权的构建 profile 与工具链版本，固定 commit + patch/worktree。
2. 编译 Agent 只调用允许的构建工具，失败不暗改候选。
3. 测试 Agent 只调用受控部署/测试工具，设备 ID 和产物身份可验证。
4. 持久化设备租约、租约丢失时隔离，不因超时就把设备当成空闲。
5. 刷写、重启、串口恢复的安全边界及人工接管流程。
6. 独立正确性参考、真实原始性能样本、基线/候选交替评测与噪声判据。
7. 本地用户/容器权限隔离，Worker 不可访问主控凭据或任意主机文件。

这些未验证条件满足前，本项目只能用于演示与协议开发。
