# JSON 驱动的固定 workflow

实验只改变 JSON 与 skill。`scripts/workflow.py` 是固定入口，`adapters/experiment.py` 解释实验输入，`core_v1` 持久化并串行派发，worker 消费冻结任务并返回结构化 receipt。核心不认识 Image、UnixBench、copies 或频率，不展开矩阵、不选择模型、不执行 shell。

当前实现是可验证的 plan/demo 编排重构，**不是已完成的真实 agent/K3 executor**。`adapters/offline_agent.py` 是离线替身，不理解自然语言、不调用模型、不执行工具。`real` 被拒绝；prompt、skill 和参数均不能解锁它。不能把 `succeeded` 解释为镜像或 benchmark 已通过。

## 实验输入

完整输入见 [offline.json](../examples/workflow/offline.json)。字段只有：

|字段|语义|
|---|---|
|apiVersion|固定 `k3.experiment/v1`|
|key|批次幂等键；修改任务、prompt 或 skill 后使用新 key|
|mode|`plan` 只保存计划；`demo` 调用固定离线 worker|
|prompt|全局任务目标|
|skills|全局 SKILL.md 路径列表，可省略|
|tests|有序列表，每项包含 id、prompt、params、可选 skills|

任务的 `params` 是数据对象，测试参数约束由未来对应工具验证。当前 demo 接受某个参数不表示硬件支持该参数。copies、rounds 等不会由核心猜测或自动扩展；任务内部如何执行由 worker/skill 负责。需要按轮保存调度状态时，在 JSON 中明确列出各轮任务。Build→Test 依赖顺序也由列表表达；前项未完成不会执行后项。

skill 相对路径相对于 JSON 所在目录。提交前读取正文、原路径、baseDir 与 SHA-256，连同任务一起冻结进 request.json；执行使用冻结正文，源 skill 后续变化不影响已提交任务。附属文件/脚本不被隐式加载，也没有执行权限。新提交读取修改后的 skill，同 key 内容变更会拒绝。相同全局/任务 skill 不应重复列出。

输入最多 256 KiB，1..128 个任务，每任务最多八个 skill、每个正文最多 64 KiB；限制按冻结后的总大小再次检查。拒绝重复 JSON 键、重复任务 ID、未知外层字段和符号链接 skill。参数中的 command 只是字符串数据，入口不执行它。不要在 JSON/skill 中存放凭据；字段名过滤不能检测正文中的秘密。

## 固定命令

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/workflow.py validate --spec examples/workflow/offline.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/workflow.py run --spec examples/workflow/offline.json --root .workflow/json-demo
PYTHONDONTWRITEBYTECODE=1 python3 scripts/workflow.py status --root .workflow/json-demo
PYTHONDONTWRITEBYTECODE=1 python3 scripts/workflow.py pause --root .workflow/json-demo
PYTHONDONTWRITEBYTECODE=1 python3 scripts/workflow.py resume --root .workflow/json-demo
PYTHONDONTWRITEBYTECODE=1 python3 scripts/workflow.py cancel --root .workflow/json-demo --key offline-example-001
```

validate 不创建状态目录、不调用 worker。run 提交后串行执行自己的任务；重跑同一 JSON 幂等返回原结果。较早的未完成批次会挡住后来批次，run 不替用户执行其他批次。resume 只清除暂停标志，再次 run 才推进。run 未完成时退出码为 2，完成为 0。

这是同步离线入口：一个进程运行时持有 root 锁，其他进程无法同时查询或暂停；不宣称具备长时间硬件任务的在线控制能力。锁只覆盖同 root，不是跨 root 的板卡锁。

## 保留的可靠性边界

- 一次只执行一个任务，前一批次完成后才进入下一批次。
- 请求、skill 正文、adapter 身份被绑定；派发前后核对请求与 adapter。
- 状态与事件同一次原子替换并 fsync。每 attempt 有独立目录及结果哈希。
- receipt 必须匹配 taskId/requestHash；未知资源、无效结果或异常进入 needs_attention，阻止后续任务。
- 启动时把 interrupted running 隔离，不自动重放。运行/未知任务不能通过 cancel 释放资源。
- 用户入口不开放自动重试。底层保留原 v1 的显式离线通信重试合同，仅允许一次、先持久预留预算，不能借此触发硬件恢复。

当前全状态文件适合小批次离线 MVP；没有声称支持无限历史。adapter 指纹覆盖 worker 入口文件，不是完整依赖的安全沙箱。`checks/resources` 在 demo 中仅为模拟 receipt，真实生产必须来自受信工具证据。

## 真实接入尚缺的部分

固定 Pi worker 必须读取冻结 prompt/skill，核对实际模型配置，调用受限且经过审查的工具。工具负责实际 Image/config/Build ID、copy/frequency、原始结果、资源释放等检查，不能接受 agent 自述成功。跨进程/旧 executor 的板卡 lease、长任务生命周期与取消清理也必须由真实接入实现。真实模型接入须遵循安装版 Pi 文档，不能把 mock 换成任意 shell。

这些是一次性的生产能力接入，不是每个实验新增脚本。**只有生产接入完成后，才能实现“真实测试只改 JSON/skill”**；当前已实现的是对应的固定离线输入与编排路径。

现有 Node/Python legacy 路径保留，避免热修改正在运行的实验。新路径不 import、生成或调用 per-Image 脚本。用户先前指定的 `/home/jingyu/workspace/k3-agent-workflow-core-v1` 仍是未来真实 Image 测试默认工作树；本次在当前仓库实现与离线验收，尚未同步到该目录，不自动切换生产执行器。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/workflow.py
node --test tests/*.test.ts
```

均为无构建、无模型、无板卡的离线检查。Python 覆盖 JSON 替换、skill 冻结、幂等、FIFO、单实例、错误 receipt、崩溃保护与 CLI 重跑。Node 覆盖保留入口的原有行为。

2026-10-14 验收：新路径 26 项通过；原 Node/Pi mock 回归 77 项通过；示例 validate 通过。Node 回归首次受沙箱 loopback 监听限制（EPERM），授权在沙箱外重跑后全部通过。无真实模型调用、构建或硬件测试。
