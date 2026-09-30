# CSRRSI FAST + PTP 真实 Build/UnixBench 实验

## 范围与身份

- 时间：2026-09-30 02:24–03:24 UTC。
- 实验 ID：`exp-42998f0c-0256-4bf6-a2f7-960ae4be2c46`。
- Main：`openai-codex/gpt-6.1-sol / medium`；Build/Test：`openai-codex/gpt-6-luna / medium`。各阶段 `runtime.json` 记录了实际模型和 thinking level。
- Main 审核了 CSRRSI/FAST gate 的释放路径。该工作区已经是 `fence rw,w` + `sw zero`，本轮没有额外源码替换（`replacements: 0`）。
- 构建使用原有 `.config`：`IEE=y`、`IEE_GATE_CSRRSI=y`、`PTP=y`、`IEE_GATE_CSRRSI_FAST=y`；可选 `CREDP=y`、`IEE_SIP=y` 也保持开启。配置未被修改。
- 没有 `clean`、`defconfig` 或自动重试。

## 结果

Coordinator 终态为 `succeeded`。Image 构建成功并完成一轮板端 UnixBench：

| 证据 | 结果 |
|---|---|
| 1-copy System Benchmarks Index | 571.6 |
| 16-copy System Benchmarks Index | 3594.9 |
| Image SHA-256 | `2bc7b5eb8c7c123e2061bc495904341d9dea15aaa21bb2bfe708e840b390bfd1` |
| Kernel Build ID | `0800a7653760e56216bd919eebda1dee4484e333` |
| Kernel release | `6.18.3+` |
| 配置与源码检查 | `configUnchanged: true`、`sourceUnchanged: true` |
| 结果门禁 | status、exitCode、scores、anomalies、rawResults、imageIdentity、cleanup 全部 `true` |
| 清理 | runner 报告 relay-off 命令已确认；串口释放检查通过后释放远端 lease |

本轮只验证当前配置与这一条执行路径。没有旧版本或同条件对照，**不能据此声称 AMO 释放修改带来性能提升**；本轮也没有新增 AMO 释放改动。

## 本机证据位置

原始实验文件放在 gitignored 的 `.workflow/`，没有随本文提交：

```text
.workflow/real-1790735010596/run-summary.json
.workflow/real-1790735010596/runs/exp-42998f0c-0256-4bf6-a2f7-960ae4be2c46/build/result.json
.workflow/real-1790735010596/runs/exp-42998f0c-0256-4bf6-a2f7-960ae4be2c46/test/result.json
.workflow/real-1790735010596/runs/exp-42998f0c-0256-4bf6-a2f7-960ae4be2c46/test/evidence.json
```

镜像、板端原始结果、脱敏远端日志和本地会话记录都保留在本机，不应提交或公开。
