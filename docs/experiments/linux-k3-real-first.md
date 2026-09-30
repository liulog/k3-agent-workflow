# 首次真实执行：构建成功，板测中断

## 实验范围

- 主 Agent：真实 Pi RPC 会话，`openai-codex/gpt-6-astra`。
- 主 Agent 完整读取三个 k3-auto skills，经 workflow 工具提交唯一实验。
- Build/Test 使用固定脚本，不另开 Luna，不执行模型生成的 shell。
- 构建：`../linux-riscv-gate` 的现有 `.config` 和未提交源码修改。
- 测试：`../k3-auto`，目标为同一 Image 的单轮 UnixBench。
- 实验 ID：`exp-0f6f311e-b38c-4ed8-954b-37c00da0f6c5`。
- 本地证据：`.workflow/real-1790686621839/`，不提交原始日志、凭据或 Image。

## 已确认的事实

| 阶段 | 结果 |
|---|---|
| 主 Agent 派发 | 调用了 `workflow_read_skills`、`workflow_execute_linux`，正常结束提交轮 |
| 构建 | 2026-09-29 12:57:07 UTC 开始，13:01:28 UTC 完成，约 4 分 21 秒 |
| 配置/源码检查 | 构建前后 `.config` 字节一致；记录的源码指纹未变化；没有 clean/defconfig |
| 产物 | Image 为 46,152,704 字节；远端 TFTP 上传 SHA-256 与本地产物一致 |
| 板卡启动 | 串口记录显示指定镜像启动、Linux 6.18.3+ 登录及部分内核配置输出 |
| 测试派发 | 串口记录中已下发该 RUN_ID 的 `run_unixbench_evidence.sh` 命令 |
| 板测终态 | 13:34:42 UTC 远端命令返回 143，workflow 标记 `needs_attention` |
| 主 Agent 总结 | 收到终态后模型返回 `The usage limit has been reached`，未调用结果工具，未完成总结 |

Image SHA-256：

```text
7170a9b9ddb99ba72bb3feff0480e09c9310a7113470dff254a3d3d286fbea6f
```

本地 vmlinux Build ID：`5f5a1247e06f14b27651ffd4773bc19bdb964d2a`。没有采集到板端完整身份核验结果，不能把本地 Build ID 当作已经验证的板端身份。

## 为什么不能判定成功

- 退出码 143 符合被 SIGTERM 终止的惯例，但现有证据不能确定信号来源；不能归因为内核崩溃。
- runner 最后保留的状态 JSON 仍为 `running`，最后一次更新时间是启动阶段，不能据此认为现在仍在测试。
- 未取得本轮完整 status/exit-code、1-copy/16-copy 成绩、异常扫描与原始结果集合。
- `anomalies=[]` 仅是中断前 runner 的观察，不是完整无异常证明。
- 没有最终 `test/result.json`，没有有效性能数字；没有把本次标记为 succeeded。

## 次日只读核查及收尾边界

2026-09-30 01:43 UTC，经跳板机重新核查：无 runner/minicom/picocom/screen，原协作锁不存在；板卡 ping/SSH 不可达。未执行重新上电、再次 benchmark、kill 或删除锁。

本工具的失败路径原本保留协作锁，因此不能声称锁由本流程正常收尾移除。锁消失和终止信号的来源均未确认。板卡不可达也不能证明物理断电。

取回原 campaign JSON 和该 RUN_ID 的串口日志，保存在 `recovery-evidence.json`；只读核查记录保存在 `recovery-inspection.log`。原 `run-summary.json` 与历史事件不作成功状态回写。

## 针对此次暴露的问题

1. 主 Agent 完成轮也必须检查最终 assistant `stopReason` 和非空文本；不能把 `agent_settled` 等同成功，也不能打印 `undefined`。错误另存 `main-error.json`，不覆盖编排器的事实结果。
2. 远端包装器处理 TERM/HUP/INT，使受控 runner 有机会走既有 finally 收尾。通过假 runner 信号测试验证；对 SIGKILL、掉线、主机故障仍不能保证清理，必须核查，不自动抢锁。
3. 本次没有自动重试真实构建、模型总结或板测。若要取得完整跑分，需要明确启动新一轮，并重新检查板卡占用与主 Agent 可用额度。

相关修正通过离线回归；未重新在硬件上验证。此次结论是 **真实主 Agent 提交和编译已跑通；真实板测已进入启动/派发阶段，但完整板测与结果总结尚未跑通**。
