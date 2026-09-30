# 开发验证与图稿维护

[返回 README](../README.md)

以下命令均在仓库根目录执行。历次验证结果见 [验证记录](verification.md)。

## 测试范围

当前自动测试覆盖：

- 状态流转、依赖、单槽互斥、暂停/恢复、排队与运行中取消；
- 幂等冲突、5 次实验预算、重启恢复、workflow 隔离；
- 源码/产物篡改、符号链接、错误 identity、错误指标和正确性失败；
- HTTP token、Origin 拒绝、单服务锁、SSE 补收、订阅者排他；
- RPC prompt 拒绝、损坏 JSONL、Unicode 分隔符、提前 `agent_end`、模型报错、超时、进程消失、取消；
- 扩展工具注册、完成通知、默认不开续轮、显式 follow-up、投递游标、detach 清理；
- plan-only 配置、技能快照、只读工具、完整分页读取证据、计划哈希交接、禁止执行字段、读取拼写纠正与失败关闭。

当前离线自动测试 **72 项通过**；真实双 Luna 只读预演和真实构建/板测均为人工启动，不放入默认测试。首次板测中断见[报告](experiments/linux-k3-real-first.md)；后续 CSRRSI FAST + PTP 实验完整通过，见[报告](experiments/linux-k3-csrrsi-fast-real.md)。

这是运行时验证，不是 TypeScript 静态类型检查。本阶段没有运行 tsc、打包或安装依赖。

## 架构图源码

使用本地 `architecture-drawer` skill 生成、评估两张 SVG，并复制到 `docs/architecture.svg` 与 `docs/sequence.svg` 供架构文档展示。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 output/20260603_architecture/gen_architecture.py
```

默认查找 `~/.pi/agent/skills/architecture-drawer`，可用 `ARCHITECTURE_DRAWER_HOME` 覆盖。

- [生成脚本](../output/20260603_architecture/gen_architecture.py)
- 设计契约：[协作图](../output/20260603_architecture/brief.json) / [时序图](../output/20260603_architecture/sequence-brief.json)
- 图形验证报告：[协作图](../output/20260603_architecture/validation.txt) / [时序图](../output/20260603_architecture/sequence-validation.txt)

生成目录中的 SVG/PNG/PPTX 是可再生文件，不纳入 Git；文档引用的 SVG 单独提交。PNG 可使用本机 ImageMagick 回退。可编辑 PPTX 依赖 `python-pptx`；当前机器未安装，因此没有生成 PPTX，也没有擅自安装依赖。
