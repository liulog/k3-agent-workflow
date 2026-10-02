# 真实板测可靠性与有界重试

默认 demo/plan-only 不编译、不访问硬件。Coordinator 的真实 one-shot Worker 仍不自动重试；下面的重试只适用于明确授权的串行 matrix 固定执行器，不能由模型文字触发，也不是 Pi 模型 API 自动重试。

## 有效结果门禁

发布 RUN_ID、UnixBench 输出了分数、远端 complete、本地最终成功是不同阶段。只有 status、退出码、完整 1-copy/16-copy 子项与总分、异常扫描、原始结果、Image SHA-256/板端 config hash/Build ID/kernel.release、清理及 frequencyLocked/frequencyHeld/frequencyRestored 全部通过，才计入成绩。

- 锁频只操作 K3 板端，各在线 policy 固定为其 performance 默认最高频率；跨轮验证相同 policy→目标向量。跳板机不改频率。
- 开始前保存原 governor/min/max；锁定后读回，结束核验并恢复。立即保存远端 `.frequency.json`，中断也能留证。
- 80 分钟检查点检查串口新增 U-Boot、boot ID 与 kernel.release，重启或身份不可读即停止，不把后续默认内核上的跑分算成功。
- 在既有启动/80 分钟检查点及最终采集记录 online CPUs、SSH 会话允许 CPU 集合、cpuset effective、cpu.max 与采样到的 UnixBench 进程 CPU 亲和性/cgroup。最多记录 128 个进程。不高频轮询、不改 taskset/cpuset。
- CPU 环境记录是观测，不代表连续验证。16-copy 是 16 份并发，不保证用了全部 16 个 CPU；缺少历史亲和性记录不能补写为已验证。
- 新启动的 normal 构建除复制 RANDSTRUCT 种子外，还继承匹配的 Kbuild seed 命令记录和 hash header，并核验实际 seed/plugin header；pool-only 使用其隔离 worktree 的显式种子生成入口并核验。仅复制 seed 文件可能被 if_changed 重新随机生成。当前已经构建的六个 normal Image 确实存在不同种子，新代码没有重建或改写这些历史样本，不把其差值全部归因于 gate。
- 异常/清理失败保留租约；SIGTERM/SIGHUP/SIGINT 只让 owned runner 进入收尾。SIGKILL、网络断线、本地 SSH 退出不证明远端任务已经取消。

## 重试策略

| 故障 | 自动行为 |
|---|---|
| SSH exit 255 / SSH deadline exceeded | 仅显式开启后允许一次恢复重测 |
| 等待跳板机网络恢复 | 每 300 秒读状态；不创建新 RUN_ID、不耗掉硬件预算 |
| panic/Oops、重启、认证失败 | 停止，人工处理 |
| 身份、频率、结果门禁失败 | 停止，不靠重测掩盖失败 |
| runner/串口/板端任务占用、其他租约、未知状态 | 拒绝恢复，不抢占 |
| 电源操作超时/结果不确定 | 已保留本次预算，停止；不无限重置 |

分类先看非网络错误：例如同时出现 panic 和 SSH255，仍按 panic 处理。结果已经有失败门禁时也不归为纯网络故障。

固定恢复步骤：

1. 检查跳板机 runner/terminal、sudo fuser 的串口输出，确认板端 SSH 可读且没有 benchmark；空串口输出或缺失板端状态不能算通过。
2. 校验失败 RUN_ID 的 owned inactive 租约；若不存在则创建该 RUN_ID 的租约，拒绝其他 owner 或任何 runner.pid。持有租约后再次核验资源。
3. **在任何 power 操作前持久化** `communicationRecoveryAttempts` 与 `recoveryEvents`。进程重启、watchdog 重启或新 RUN_ID 不重置同一逻辑轮次的预算。
4. 掉电 30 秒，上电后等待 45 秒；再次检查板端 SSH、任务、runner、串口，校验 owner/PID 后释放本次恢复租约。这些是继电器命令确认，不是独立电气测量。
5. 保留旧目录、failure/evidence 和 `attempts`；为重测生成新 RUN_ID、独立目录，清除上一尝试的 scores/checks/frequency。重测仍需全部门禁通过。

每轮最多 **一次通信重测**（总共至多两次板测尝试）。一次已准备好的 watchdog 恢复可继续派发重测，不再多做一次电源循环。资源核验失败不会停止其他 runner。

## 入口

预构建 Image 的七配置 campaign（不构建、不安装）：

```bash
export K3_MATRIX_ROOT="$PWD/.workflow/my-matrix"
export K3_MATRIX_ARTIFACT_ROOT=/path/to/prebuilt-artifacts
export K3_ROOT=/path/to/k3-auto
python3 scripts/unixbench-matrix.py --plan   # 只读本地产物核验
python3 scripts/unixbench-matrix.py --status
# 只有用户授权板测和一次通信恢复后才执行：
python3 scripts/unixbench-matrix.py --run --communication-retries 1
```

该入口仍是 `c4381d440` 的任务专用配置列表，产物需要 Image、config/config.sha256 和 build report（或预存 Build ID）；不是任意构建配置框架。默认 `--run` 不重试。手动恢复只允许一个 inactive blocked 通信失败：

```bash
python3 scripts/unixbench-matrix.py --resume --retry-failed
```

若不启用恢复，可明确跳过一个失败配置：`--resume --skip-config <id>`。跳过不产生平均值，终态为 complete-with-skips。

独立五分钟 watchdog（需另获电源恢复授权）：

```bash
python3 scripts/matrix-network-watchdog.py \
  --root "$K3_MATRIX_ROOT" --authorize-power-cycle-retry
```

watchdog 有 singleton 锁，仅在取得 dispatcher 锁且 blocked/current 为空时干预；不会杀 dispatcher、runner 或 benchmark。读取失败/网络不可达时等待；非网络故障、预算耗尽、未知资源只报告。它只恢复 unixbench-matrix 的预构建任务 schema，不应用到 Coordinator SQLite 状态或 normal/pool-only campaign。

`normal-matrix.py` 与 `pool-only-matrix.py --build-and-run` 是当前实验的独立目录构建入口，**启动即可能编译**，只能在各自构建和板测范围明确授权后执行。两者也支持 `--communication-retries 1` 的独立恢复授权，使用同一 `reliability.py`，网络中断时由当前 dispatcher 每五分钟等待；不需要上述 watchdog。默认仍为 0。normal/pool-only 当前不提供通用 resume CLI，遇到非网络故障或恢复被拒绝后停下交由操作员处理，不删除状态后重跑。

## 状态与证据

本机 `.workflow/` 保存 state.json、dispatcher.log、每个尝试的 spec/result/failure/evidence、失败历史和恢复事件；全部 gitignored。remote runner.pid、owner 与 root-visible 串口是资源证据，不能仅靠本地 dispatcher.pid 猜远端状态。

并发启动无法取得 dispatcher 锁时不得改写活动 state。重试后的频率向量仍必须与 campaign 基准一致；仅完整三轮配置计算平均值。网络恢复不重建 Image、不编辑源码/.config、不 force-push。

## 离线验证

```bash
node --test tests/*.test.ts
python3 tests/real_contracts.py
python3 tests/reboot_guard.py
python3 tests/reliability.py
```

这些检查使用夹具、临时文件和 mock，不联系模型、不执行编译、不调用真实 k3ctl/SSH/电源。验证故障分类、关机前预算持久化、只读网络等待、异主租约/PID/串口/任务拒绝、失败留证、新 RUN_ID、次数上限、活动 dispatcher 不干预。通过不代表 SIGKILL/硬件故障或网络恢复已做完真实端到端验收。

**更新代码不会替换已启动 Python 进程中加载的执行逻辑。不要为了部署重试改动中断正在跑的实验。** 后续新启动的 stage/campaign 才加载新版本。
