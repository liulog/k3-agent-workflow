# MVP 实验：真实 Build Luna → Test Luna/k3-auto 只读交接

## 目标与边界

验证“主控提交一个 linux-riscv-gate 编译任务描述 → Build Luna 输出计划 → Test Luna 阅读 k3-auto skills 并接续规划 → 结果回传”。

主控由单次实验脚本代替，没有启动 Astra 模型；两个 Worker 使用真实 Pi RPC 与 `openai-codex/gpt-5.6-luna`。这不是模拟模型输出的通过记录，但也**不是编译或板测成功记录**。

- Node.js：v24.19.0。
- Linux 资料：本机 `linux-riscv-gate` 中 6 个选定文件的只读快照。
- k3-auto：submodule commit `501f70f8ec23b0d2d436bb52729301cbf33f586e`。
- Test skills：`k3-benchmark`、`k3-lab`、`k3-status`。
- 工具：两个 Worker 均只有经过白名单和哈希校验的 `read`。
- 未运行：编译器、构建脚本、k3ctl（包括 --dry-run）、SSH、串口、继电器、benchmark。
- 模型 OAuth 使用 Pi 原有认证；未读取或复制板卡/跳板机私有配置。

## 实验一：发现真实模型的路径拼写错误

实验 ID：`exp-a43e2a0c-d344-48fe-86b1-cb4d7d7c90ac`。

本地证据根目录：

```text
.workflow/plan-experiment-1790666893544/
```

Build Luna 完成计划并进入 Test。Test 读取 `k3-lab/SKILL.md` 时漏写实验 UUID 中的一段，访问了不在白名单中的路径。原适配器立即终止该任务，状态为 failed，未放宽白名单或读取越界文件。

据此做了局部修正：

1. 提示词优先提供短相对路径，减少重抄 UUID。
2. 受限 read 遇到未知路径时返回明确错误和合法相对路径。
3. 每阶段最多允许 3 次失败读取供模型纠正；失败不算读取证据。
4. 非 read 工具、越权成功读取、缺失 guard receipt、未完整读完必需文件，仍然失败关闭。

失败记录未删除；没有自动无限重试。

## 实验二：完整链路通过

实验 ID：`exp-da4e914d-9313-4f68-9cf3-813daea585c0`。

本地证据根目录：

```text
.workflow/plan-experiment-1790667064109/
```

执行入口：

```bash
node scripts/plan-experiment.ts --config planning.local.json --real-models
```

事件序列：

```text
experiment.queued
build.started
build.succeeded
test.started
experiment.succeeded
```

| 验证项 | Build Luna | Test Luna |
|---|---|---|
| Pi 返回的模型 ID | gpt-5.6-luna | gpt-5.6-luna |
| 最终 stopReason | stop | stop |
| 激活工具 | read | read |
| 完整读取的文件 | 6 份 Linux 资料 | 3 skills + 2 文档 + Build 计划 |
| read 调用次数 | 8（Makefile 分页） | 6 |
| 失败读取 | 0 | 0 |
| 实际命令执行 | 无 | 无 |

Build 计划 SHA-256：

```text
f7886d37e25c52979ee296a20afa503c492c2135274c2d2e4873a2e46a1ab56b
```

Test 计划中的 `buildPlanHash` 与它一致。Test 计划 SHA-256：

```text
d6b75e35efb80b7488f6a2d3631377716327cc59b30e86c8ed78fd167f2f5961
```

结果标记：

```json
{
  "mode": "plan-only",
  "planningCompleted": true,
  "buildExecuted": false,
  "boardAccessed": false
}
```

## 模型实际产出的内容

Build Luna 提出了保留现有 `.config`、不擅自 clean/defconfig、使用 RISC-V 交叉工具链构建 Image 的计划；注意到了 `scripts/build_kernel.sh` 的默认配置覆盖行为，列出工具链、配置和归档路径尚未确认。

Test Luna 在计划中引用三个 skill，并覆盖了：

- 无残留 runner / 串口冲突等前置检查；
- 已核验 Image 的 TFTP 交接及单次启动单 RUN_ID；
- UnixBench 的 status、exit-code、完整成绩、异常扫描、原始结果联合判据；
- 日志归档、异常处理及关机/串口清理要求。

这些是计划文本，不曾执行。Test 输出仍复述了一些 Build 步骤，摘要也偏向端到端构建；未来接入真实任务前，需要人审并收紧角色职责、步骤顺序和占位符，而不能把这份 JSON 直接作为可执行脚本。

## 通过与未通过的范围

**已验证：** 两个真实模型子进程可启动、显式 skill 注册与完整读取、受限 read 执行、结构化 JSON 输出、编译计划哈希交接、HTTP/RPC/SSE 完成回传。

**未验证：** 主 Astra 模型的自然语言派发/交互式 TUI、真实内核构建、工具链、现有 `.config`、完整源码复现、私有凭据、板卡可达性、设备独占和刷写安全、benchmark 性能结果。

离线回归共 46 项通过，覆盖上述协议与失败分支。真实实验日志/源码快照可能包含项目内容，因此只留在 gitignored 的 `.workflow/`，不推送原始日志、令牌和本机配置。
