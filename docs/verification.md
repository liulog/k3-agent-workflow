# 初版验证记录

环境：Node.js v24.19.0，Linux；使用本机已安装 Pi 的导出检查扩展。

| 检查 | 结果 |
|---|---|
| `node --test tests/*.test.ts` | 29 tests，29 pass，0 fail |
| `node scripts/demo.ts` | 异步提交→模拟 build→模拟 test→SSE 完成通知→幂等重提交，通过 |
| `node scripts/check-extension.ts` | 真实扩展入口加载，4 个 schema-backed 工具与 `/workflow` 注册，通过 |
| 初版架构 SVG evaluator | 100 分，0 FAIL；7 个自由说明文字的 orphan-label WARN |
| 初版架构 PNG | 使用本机 ImageMagick 生成并查看；README 使用保留原生箭头 marker 的 SVG |

测试使用临时目录并清理，不保留后台服务。RPC 测试使用 Node 假 Pi 子进程，实际经过 stdin/stdout JSONL，包括提前 agent_end、Unicode 分隔符、超时及错误分支。

没有执行：npm install、tsc、应用构建/打包、真实汇编编译、模型 API 请求、SSH、刷板或硬件测试。扩展加载检查是原生 Node 运行时检查，不是完整 Pi TUI 交互测试或 TypeScript 静态检查。

PPTX 导出因本机缺少 `python-pptx` 未执行；没有为此安装依赖。图形 WARN 对应图外的连接协议、SSE 说明与图例/未来能力说明，不涉及节点碰撞或箭头交叉。

## 协作与协议图修订

仅修改文档和绘图源文件，未改变运行时代码。根据 `extension/workflow.ts`、`src/server.ts`、`src/rpc-worker.ts` 核对链路：

- 独立展示 Astra、Build Luna、Test Luna，以及同进程扩展、非模型编排器。
- 增加完整时序 SVG、协议表、实际消息样例、文件交接与接受/成功的区别。
- 明确默认无模型模式、可选 RPC 模式与尚未实现的 k3-auto/开发板集成。
- 两张图均已生成 PNG 并查看；README 提交的是 SVG（ImageMagick 的 marker 渲染不完整）。

| 修订图 | evaluator | 语义检查 |
|---|---|---|
| `docs/architecture.svg` | 100 分，0 FAIL | 6 个图外标签 orphan-label WARN，0 FAIL |
| `docs/sequence.svg` | 100 分，0 FAIL | 2 个底部说明 orphan-label WARN，0 FAIL |

设计契约与详细报告位于 `output/20260603_architecture/`，其中 `sequence-*` 为新增时序图的记录。

## Linux → k3-auto 只读预演修订

- 离线测试：46 项通过（含只读路径与完整分页证据、skill 注册参数、计划 hash 交接、错误模式和重启处理）。
- 静态资料检查：使用真实 linux-riscv-gate 和 submodule 文件通过，不调用模型或执行脚本。
- 主控扩展导出检查：5 个工具注册通过，新增 `workflow_plan_linux`。
- 假 Pi 端到端实验：通过。
- 真实 `openai-codex/gpt-5.6-luna` 双 Worker：第一次因路径拼写错误被拒绝，局部修正后第二次通过。Build 完整读 6 个文件，Test 完整读 3 skills、2 文档及 Build 计划，计划 hash 一致。
- 未编译、未执行 k3ctl/SSH/串口/上下电/benchmark；主 Astra 模型由脚本代替，交互式主会话尚待用户部署验证。

详细证据、限制和复现入口见 [MVP 实验报告](experiments/linux-k3-plan-smoke.md)。
