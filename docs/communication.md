# Agent 协作与通信协议

本文对应当前源码，而不是尚未实现的目标架构。两张图均以 **Pi RPC demo 模式**为例；默认无模型 demo 使用相同 HTTP/SSE 和任务状态机，但不启动 Luna 子进程。

- [协作与协议图](architecture.svg)
- [一次实验的时序图](sequence.svg)
- [状态机与权限边界](design.md)

## 1. 谁和谁通信

```text
Astra 的 Pi 进程
  ├─ Astra：提出候选、分析结果
  └─ workflow 扩展：本地 tools ↔ HTTP / SSE
                       │
               workflowd（不是模型）
                       ├─ 子进程 stdin/stdout ↔ Build Pi / Luna
                       └─ 子进程 stdin/stdout ↔ Test Pi / Luna
```

三个 Agent 不使用“共享群聊”，不需要 MCP、Redis、消息中间件，也不共享完整聊天上下文。编译/测试 Agent 每个阶段使用新进程和新上下文，只有编排器知道阶段依赖与任务状态。

主 Pi 与扩展是**同一进程**；workflowd 是独立长驻进程；Luna Worker 是按需创建的子进程。三个 Pi 不保证全程同时存活：一个实验中 build 结束并退出后才会创建 test 子进程。不同实验可以流水线重叠。

## 2. 主 Agent 发任务：本地 tool → HTTP JSON

Astra 调用的是注册在自己 Pi 中的工具：

```json
{
  "key": "candidate-001",
  "candidate": ".text\nret\n",
  "hypothesis": "验证异步通信，不宣称真实性能收益"
}
```

工具 `workflow_submit` 自动补充 `profile: "demo"`，向当前 attach 的 workflow 发送：

```http
POST /workflows/asm-demo/experiments HTTP/1.1
Authorization: Bearer <本机令牌>
x-workflow-owner: <扩展本次连接的随机 ID>
Content-Type: application/json
```

```json
{
  "key": "candidate-001",
  "candidate": ".text\nret\n",
  "hypothesis": "验证异步通信，不宣称真实性能收益",
  "profile": "demo"
}
```

服务在返回之前固化候选字节、计算 SHA-256，并提交 SQLite 记录。HTTP 响应状态为 **202**，body 是实验对象；工具只向模型返回摘要，例如：

```json
{
  "id": "exp-<uuid>",
  "status": "queued",
  "sourceHash": "<sha256>",
  "simulated": true
}
```

这只是“已持久化并排队”，不是“已编译”。同一个 `key` 重交相同数据返回同一实验；数据不同则 409。取消当前 Pi 工具调用不等于取消已接收的后台任务。

HTTP 202 也用于幂等重交，此时对象可能已处于终态，应读取 `status`，不能只看状态码。

## 3. 调度 Worker：Pi RPC over stdin/stdout

编排器通过子进程管道通信，不是给 Pi Worker 启动 HTTP 端口，也不连接它们的 TUI。

### 下行：stdin

一行一个 JSON，末尾 LF：

```json
{"id":"job","type":"prompt","message":"你负责 build 阶段。读取指定 candidate.s；在本任务目录写入 artifact.txt 和 result.json；结果必须满足指定 JSON contract。当前仅模拟，不编译、不访问开发板。"}
```

外层是 Pi RPC 命令，内层 `message` 是阶段委派提示词，包含明确的路径与结果契约；**不是把业务 JSON 直接当成 Pi 协议命令**。

`id: "job"` 在当前实现中可复用，因为每个 Worker 子进程只处理一个业务任务。它不是全局实验 ID。

### 上行：stdout

以下为删减了无关字段的示例：

```json
{"id":"job","type":"response","command":"prompt","success":true}
{"type":"tool_execution_start","toolName":"read","toolCallId":"...","args":{"path":".../candidate.s"}}
{"type":"message_end","message":{"role":"assistant","stopReason":"stop","content":[{"type":"text","text":"模拟阶段产物已写入。"}]}}
{"type":"agent_end","messages":[]}
{"type":"agent_settled"}
```

- `response.success: true`：prompt 已接受，不代表模型执行完毕。
- `tool_execution_*` / 消息事件：进度与诊断，写入阶段 `rpc.jsonl`。
- `agent_end`：一个底层 agent run 结束；可能还有重试或恢复，**不能用来派发下一阶段**。
- `agent_settled`：Pi 不会再自动继续。适配器还检查最终 assistant 的 `stopReason`，等待子进程退出，随后 Engine 校验磁盘产物。
- stderr 单独进入 `stderr.log`，不当作 JSONL 解析。

当前适配器仅用 LF 分帧，保留 JSON 字符串内合法的 Unicode 分隔符；有超时、日志大小上限和取消处理。

## 4. 编译 Agent 如何把结果给测试 Agent

**不直接给测试 Agent 发消息。** 数据由编排器进行可信度检查并交接：

```text
Build Agent 写入 build/artifact.txt + build/result.json
     ↓
workflowd 校验 sourceHash、输出格式和实际文件
     ↓
workflowd 登记 artifact.path + artifact.sha256
     ↓
创建 Test Pi 子进程，将路径和 hash 写进测试 prompt
     ↓
Test Agent 读取文件，写 test/result.json
     ↓
workflowd 再次检查产物 hash、结果格式和 correctness
```

本地文件是数据面；Pi RPC 是任务控制面。并不是在 stdout 中传 ELF/固件，也不是让两个 Worker 互读会话。

当前 build 结果契约：

```json
{
  "simulated": true,
  "kind": "build",
  "sourceHash": "<candidate.s 的 sha256>",
  "artifact": "artifact.txt"
}
```

当前 test 结果契约：

```json
{
  "simulated": true,
  "kind": "test",
  "artifactHash": "<登记产物的 sha256>",
  "correctness": true,
  "samples": [100, 101, 99, 100, 100],
  "unit": "synthetic-cycles"
}
```

这些固定数字只测试链路，不是 benchmark。未来真实固件、原始测量数据和正确性参考需要另外设计契约，不能直接取消 `simulated` 标记就用于硬件。

## 5. 返回主 Agent：SSE → Pi custom message

扩展 attach 时建立持续连接：

```http
GET /workflows/asm-demo/events?after=17 HTTP/1.1
Authorization: Bearer <本机令牌>
x-workflow-owner: <连接 ID>
```

服务端返回 `Content-Type: text/event-stream`。SSE 的 `data:` 中是我们的事件 JSON，不是 Pi RPC 命令：

```text
id: 18
data: {"id":18,"workflow":"asm-demo","type":"experiment.succeeded","experimentId":"exp-<uuid>","data":{"status":"succeeded","stage":"test","result":{"simulated":true,"correctness":true,"samples":[100,101,99],"unit":"synthetic-cycles"}}}

```

扩展收到一般进度时只更新状态栏，收到实验终态才调用：

```typescript
pi.sendMessage(
  {
    customType: "k3-workflow-result",
    display: true,
    content: "经过校验的实验结果摘要……",
    details: { eventId: 18, workflow: "asm-demo" },
  },
  { deliverAs: "followUp", triggerTurn: auto && !paused },
);
```

- **auto OFF**：结果进入会话，空闲时不额外调用模型。
- **auto ON**：空闲时触发模型分析；忙碌时排在当前任务之后，不用 steer 抢占。
- Pi runtime 发出结果 custom message 的 `message_end` 后，扩展才确认持久化投递游标；这不是又让 Astra 回一句“收到”。
- SSE 断线从事件 ID 补收；极端崩溃仍可能重放，实验提交依靠 `key` 幂等。

查询详细结果使用 `workflow_result` → HTTP GET，不强迫模型拿状态轮询。进度的服务端轮询发生在后台程序中，不消耗模型推理轮次。

## 6. 四类身份不能混用

| 标识 | 范围 | 作用 |
|---|---|---|
| `key` | 一个 workflow 内 | 重复提交去重；同 key 不允许改变候选 |
| `exp-<uuid>` | 实验 | 贯穿 build、test、文件目录、事件和结果查询 |
| RPC `id: "job"` | 单个 Worker 的管道 | 关联 prompt 请求与接受响应；Pi 事件一般不带它 |
| SSE `id` | 数据库事件流 | 重放与投递确认；同一 workflow 内可能有数字间隙 |

阶段由 `stage: build|test` 区分，当前实现没有额外的全局 build_task_id/test_task_id API。

## 7. 失败、暂停和重启时怎样协作

| 情况 | 编排器的行为 | 主 Agent 如何得知 |
|---|---|---|
| build 出错或产物校验失败 | 不启动 test；记录 failed | SSE `experiment.failed` |
| test 指标/产物身份/正确性失败 | 记录 failed，不当作优化成功 | SSE `experiment.failed` |
| 用户取消 | 等 Worker 停止后释放槽位 | SSE `experiment.cancelled` |
| pause | 不再派发新阶段，当前任务继续 | 状态栏/查询；扩展关闭 auto |
| 服务异常重启发现 running | 转 needs_attention，不自动重放副作用 | 重连后补收对应终态事件 |
| 主 Pi 关闭或 detach | 后台任务照常运行 | 再次 attach 后补收 |

图中成功路径不是绝对的事件时序保证：Pi 很快时，prompt 接受响应与部分事件可能交错；适配器等待“接受响应 + settled”两个条件，而不是依赖相邻消息顺序。不同实验的事件也可能交错，用实验 ID 区分。

## 8. k3-auto 在哪里接入（未实现）

将来应在 **测试 Worker 的受控工具/skill 层**接入 `integrations/k3-auto`，不是替换主控 SSE，也不是让开发板直接跟 Astra 聊天。

skill 本质上是给 Agent 的操作说明/资源，不是传输协议；其工具或脚本才可能通过 SSH、串口等连接开发板。具体板测协议、部署命令和授权范围尚未接线验证。

当前 RPC Worker 带 `--no-skills`，只开放 `read,write`，所以不会自动加载这个 submodule。正式启用前还需增加受控工具、设备独占租约、刷写安全边界、恢复流程及真实结果契约。

## 源码导航

| 源码 | 对应职责 |
|---|---|
| `extension/index.ts` | 模型工具参数 schema |
| `extension/workflow.ts` | tools、attach、SSE 到 Pi 消息、投递确认 |
| `src/client.ts` | HTTP 请求与 SSE 分帧/重连 |
| `src/server.ts` | 认证、HTTP 路由、SSE 发布 |
| `src/engine.ts` | build→test 状态机和产物校验 |
| `src/rpc-worker.ts` | Pi 子进程 JSONL 适配器 |
| `src/store.ts` | SQLite 任务与事件持久化 |
