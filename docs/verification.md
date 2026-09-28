# 初版验证记录

环境：Node.js v24.19.0，Linux；使用本机已安装 Pi 的导出检查扩展。

| 检查 | 结果 |
|---|---|
| `node --test tests/*.test.ts` | 29 tests，29 pass，0 fail |
| `node scripts/demo.ts` | 异步提交→模拟 build→模拟 test→SSE 完成通知→幂等重提交，通过 |
| `node scripts/check-extension.ts` | 真实扩展入口加载，4 个 schema-backed 工具与 `/workflow` 注册，通过 |
| 架构 SVG evaluator | 100 分，0 FAIL；7 个自由说明文字的 orphan-label WARN |
| 架构 PNG | 使用本机 ImageMagick 生成并查看；README 使用保留原生箭头 marker 的 SVG |

测试使用临时目录并清理，不保留后台服务。RPC 测试使用 Node 假 Pi 子进程，实际经过 stdin/stdout JSONL，包括提前 agent_end、Unicode 分隔符、超时及错误分支。

没有执行：npm install、tsc、应用构建/打包、真实汇编编译、模型 API 请求、SSH、刷板或硬件测试。扩展加载检查是原生 Node 运行时检查，不是完整 Pi TUI 交互测试或 TypeScript 静态检查。

PPTX 导出因本机缺少 `python-pptx` 未执行；没有为此安装依赖。图形 WARN 对应图外的连接协议、SSE 说明与图例/未来能力说明，不涉及节点碰撞或箭头交叉。
