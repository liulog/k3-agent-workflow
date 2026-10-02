# 单次真实执行（必须明确授权）

```bash
node scripts/real-experiment.ts --execute
```

这不是默认 demo 或 plan-only。它会调用真实模型、交叉编译器、SSH 和板卡电源控制；只在用户明确授权后运行，不放入默认测试。

当前入口刻意固定为本次实验，不做通用执行框架：

- 主 Agent：`openai-codex/gpt-6.1-sol / high`；Build/Test Agent：`openai-codex/gpt-6-luna / medium`。启动时校验实际模型与 thinking level，Main 为 high，Build/Test 仍为 medium。
- 构建目录：主仓库旁的 `../linux-riscv-gate`；测试目录：主仓库旁的 `../k3-auto`。
- 主 Agent 审核有界候选并读取 skills，经 HTTP 提交唯一真实实验，然后等待 SSE；终态到达后在同一 Pi 会话读取结果并总结。
- Build/Test 各自先审核本阶段快照，再请求固定执行器；模型不能运行任意 shell。

## Main 优化设计约束如何传入

Linux 仓库中的 `Documentation/DESING.md` 会随候选快照复制并记录 SHA-256；启动器将快照路径通过 Pi 可重复使用的 `--append-system-prompt <path>` 作为 Main 的附加系统提示传入，同时候选审核工具也返回该文件供核对。这样 Main 收到的是与本轮候选绑定的冻结版本，而不是运行时随意读取的工作树文件。

这里不依赖 `AGENTS.md`：Main 启动参数包含 `--no-context-files`，Pi 因此关闭 `AGENTS.md`/`CLAUDE.md` 自动发现。把 Linux 优化规则写入 workflow 仓库的 `AGENTS.md` 不但不会传给这个 Main，还会把单一任务约束错误地扩大为项目通用规则。设计文档只约束优化判断，不扩大 Main 的工具权限或固定执行器的候选范围；所有构建/板测仍须通过既有授权工具。

## Main Agent 上下文复用

Main 使用固定 Pi session ID `linux-k3-workflow-main`，会话文件保存在 gitignored 的 `.workflow/main-agent-session/`。每次真实 workflow 启动新的 Pi 进程，但用 `--session-id` / `--session-dir` 恢复同一会话，所以会在实验之间复用历史；当前调用再附加本次任务，不会复用 Build/Test 会话。Pi 自动 compaction 负责控制输入上下文大小，原始会话记录仍保存在本机。

同一持久 session 使用本地单写者锁，避免两个 launcher 同时写坏 session。进程被强制杀死时锁文件可能残留；确认锁中的 PID 已不在运行后，才可人工移除。会话可能包含提示、工具调用、源代码片段和实验摘要，只保存在本机，不能提交或分享。当前已运行的旧版本 launcher 使用 `--no-session`，本身无法恢复；首次持久会话会带入最近一次运行的 `main-summary.txt`，或没有时带入 `run-summary.json`（若存在），作为不可信历史资料；之后持续使用持久 session。

## 构建边界

保留现有 `.config` 和源码修改，不 clean、不 defconfig、不安装依赖。固定运行 `make -j8 ARCH=riscv CROSS_COMPILE=<现有工具链绝对前缀> KCONFIG_NOSILENTUPDATE=1 Image`（不足 8 核时减少并行数）。

保存 `.config` 原文、构建命令、源码提交/差异指纹和日志。若 Kconfig 需要更新或构建前后配置不一致，则停止，不自动改配置或重试。源码指纹覆盖 tracked diff 与选定 untracked 源文件，不是完整、隔离、可复现的源码快照；实验期间不要并行编辑或手动编译该目录。

成功后复制 Image，登记 SHA-256、内核 Build ID 和 kernel.release。使用本地文件锁避免本工具重复构建同一工作树。

## 板测边界

沿用 k3-auto 的配置、TFTP 上传和现有 UnixBench runner：

1. 获取远端协作锁，不抢占已有锁；检查设备、runner 和 root 可见串口占用。
2. 记录状态快照；SSH 不可达只表示未知，不代表已下电。
3. 上传唯一命名的 Image 并核对远端 SHA-256。
4. 包装现有 runner，只传一个 RUN_ID，不修改其源文件。在 **K3 板端**（不是跳板机）启动后、UnixBench 前，通过到 K3 的 SSH 执行固定 cpufreq 操作：读取在线 CPU policies，切到 `performance` governor 后读取每个 policy 的默认 max 频率，分别锁定并验证 K3 的 min/max 限值；测试结束后在板端恢复原 governor/min/max。Test Agent 只能请求该固定操作，不能运行任意 shell；跳板机 CPU 不会被改动。任何策略不支持、写入/读回失败或恢复失败都使测试失败，不开始跑分或标记有效。
5. runner 只传一个 RUN_ID，并在下电前收集完整证据。UnixBench 的状态/异常扫描间隔设为 **80 分钟**（上次完整跑分约 57 分钟），避免运行期间每分钟扫描干扰负载；因此故障发现、完成确认和 runner 控制的下电最多可能延迟一个检查间隔。若测试时长明显增加，应先相应调整该间隔。
6. 核对 status、exit code、1-copy/16-copy 完整成绩、异常、原始结果、频率锁定/维持/恢复证据，以及板端 `.config`、Build ID、kernel.release 身份。
7. runner 负责收尾；校验下电命令成功和串口释放后才释放协作锁。下电命令成功不等于独立电气测量。

串口忙、已有 runner、身份不符、证据不完整、收尾不确定均不能算成功。单次 Coordinator 入口失败标记 `needs_attention`，不自动重试。另有独立、显式授权的串行 matrix 通信恢复入口：同一逻辑轮次最多一次电源循环重测，五分钟只读等待网络恢复，panic/门禁/资源失败不重试，详见[可靠性与重试](reliability.md)。远端协作锁不能约束其他不遵守它的工具。

**硬件阶段不支持通过普通 cancel 杀进程。** 断线或崩溃时不能假定远端任务停止，不自动抢锁/清理；先核实该实验的 runner 与板卡状态，再人工处理。重启不自动重跑真实任务。

## 证据

每次运行保存到 gitignored 的 `.workflow/real-<timestamp>/`：

- `launch.json`、`main-rpc.jsonl`、`main-submission.txt`、`main-summary.txt`（模型失败则写 `main-error.json`）；
- `events.jsonl`、`run-summary.json`；
- `runs/<id>/build/`：配置备份、make.log、Image、构建结果；
- `runs/<id>/test/`：skill 哈希、脱敏远端日志、完整证据和判定结果。

凭据由 k3-auto 在运行时读取，不传给主 Agent；远端输出保存前按配置中的密码脱敏。日志、配置、Image 和原始结果均不得提交 Git。

首次真实运行的中断经过见[实验报告](experiments/linux-k3-real-first.md)。随后 CSRRSI FAST + PTP 的真实实验完成 Build 与一轮 UnixBench，所有结果门禁通过；详见[实验报告](experiments/linux-k3-csrrsi-fast-real.md)。两次运行均无性能基线，不能据单次跑分声称优化收益。

`tests/real.test.ts`、`tests/real_contracts.py`、`tests/reboot_guard.py`、`tests/reliability.py` 只用夹具和 mock 验证门禁、重启/取消、状态和有界恢复，不实际调用模型、编译或硬件。真实执行是否完成，以对应实验的日志和终态为准。
