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

## MVP 简化回归

- 调度从两个阶段槽位收敛为全局一个活动 Worker；合并两个 profile 的成功状态流转，结果字节只读取一次用于解析与哈希。
- 47 项离线测试通过，验证不同 workflow 无阶段重叠、按实验顺序 Build→Test、取消等待 Worker 清理、暂停让位和失败不阻塞后续任务。
- 原无模型 demo、真实资料快照 + 假 Pi 的 plan-only 实验、扩展入口检查均通过。
- HTTP/JSON、Pi RPC、SSE 接口未改动，既有输入校验、幂等、断线补收、只读边界和失败检查保留。
- 此次没有再调用真实模型，没有编译、安装依赖或访问板卡。前节 46 项及真实 Luna 的记录为简化前的历史实验，不覆盖此次变更。

## 首次真实运行与修正

- 真实 Astra 已通过 workflow 提交；现有 `.config` 构建成功且未改变配置；Image 上传哈希一致。
- 板卡已启动并下发 UnixBench 命令，但 runner 以 143 退出，任务保留 `needs_attention`，没有有效跑分。
- 主 Agent 的终态总结遇到额度限制；已补充最终 stopReason/文本检查，不再输出 `undefined`，也不改写任务事实。
- 远端包装器新增信号收尾处理，并用无硬件假 runner 验证；不保证 SIGKILL 或主机丢失时安全收尾。
- 离线回归现在为 60 项；真实全流程尚未验收通过，未自动重试模型或硬件。
- 详细经过、证据和当前边界见 [首次真实实验](experiments/linux-k3-real-first.md)。

## 敏感信息审查（基于 main `315c253`）

- 主仓库：全部本地 refs/reflog 可达的 5 个提交、71 个唯一文件 blob，以及当前非忽略工作文件。远端 main 与该提交一致；fsck 未发现不可达对象。
- 子模块：固定版本 `501f70f8` 的本地可见历史（1 个提交、16 个唯一 blob）及工作文件；不是对上游最新 main 的审计，未更新子模块指针。
- 使用本地规则检查常见 provider key、私钥头、JWT、凭据赋值、带密码 URL、sshpass、授权字符串及高熵字面量，并人工复核疑似项。未安装扫描依赖，也未向外部扫描服务上传内容。
- 未发现真实 API key、私钥、token 或非空配置密码。唯一规则命中是 `board_ssh.py` 的登录提示匹配代码，属于误报；三个 TOML 配置中的密码字段均为空。
- 两张历史 PNG 的可见内容是架构图，未见凭据，且没有文本/EXIF 元数据块。
- `.workflow/`、`planning.local.json`、`.env` 未进入已检查的历史；5 个本地 token 文件均为 `0600`。增加 `.env.*` 忽略规则，示例文件除外。
- **不等于没有任何隐私信息**：提交元数据包含作者邮箱；子模块包含内网地址、环境配置及文档中的本机路径。这些不是密钥，但是否允许公开需由维护者决定。

结论限于上述版本、范围和检测方法，不是无泄漏保证。忽略规则无法修复既有历史；若以后发现真实凭据，应先撤销/轮换，再单独规划历史清理，不自动重写已发布提交。

## CSRRSI FAST + PTP 真实实验

- 实验 `exp-42998f0c-0256-4bf6-a2f7-960ae4be2c46` 于 2026-09-30 完成真实 Image 构建及一轮 UnixBench；Coordinator 终态 `succeeded`。
- Main / Build / Test 的运行时记录分别核实为 `gpt-6.1-sol / medium`、`gpt-6-luna / medium`、`gpt-6-luna / medium`。
- 原有 `.config` 已启用 CSRRSI FAST + PTP，CREDP/SIP 也保持开启；配置与源码检查未变化。本轮释放代码已是 `fence rw,w` + `sw zero`，候选没有产生新源码替换。
- 1-copy 571.6、16-copy 3594.9；status、exitCode、scores、anomalies、rawResults、imageIdentity、cleanup 七项门禁全部通过。
- 没有性能对照，不据此宣称性能提升。完整摘要和本机证据路径见[实验报告](experiments/linux-k3-csrrsi-fast-real.md)；原始日志、Image 和证据不提交。
- 此实验使用临时 Main RPC 会话。后续真实入口改为复用 `.workflow/main-agent-session/` 下的持久 Pi session；该持久化改动通过离线测试与无模型 Pi session smoke 验证，尚未在下一轮真实 workflow 中运行验证。
