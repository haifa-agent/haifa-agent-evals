# Haifa–Harbor Adapter 契约（MVP）

> 状态：Ready for implementation
> Harbor：已发布稳定版 0.20.0
> 日期：2026-08-19

## 1. 当前 CLI 事实

- JAR 是可执行 shaded JAR，Main Class 为 `io.haifa.agent.cli.HaifaCliMain`；
- 使用 Java 21；
- one-shot 参数为 `-m/--message`；
- `--workspace` 和 `--config` 必须显式传递；
- `--timeout` 接受 ISO-8601 Duration；
- `--trace jsonl --trace-file` 可输出安全事件；
- stdout 是流式回答，不是机器评分协议；
- 当前退出码：完成 0，参数/启动错误 1，Run 未完成或超时 2。

## 2. Adapter 形式

实现一个 Harbor `BaseInstalledAgent`：

```text
integrations.harbor.haifa_agent:HaifaCodingAgent
```

它只负责：

1. 把固定 JAR 和无密钥配置放入 Task Container；
2. 在 Task Workspace 运行 one-shot CLI；
3. 把 exit、stdout/stderr 和 Trace 引用交给 Harbor。

它不负责任务选择、评分、结果聚合和报告。

## 3. 安装

- Container 必须是 Linux；
- Container 已有 Java 21 且包含 `jdk.random` 时直接复用；否则使用冻结的 Temurin 21.0.8+9 JDK archive；
- JDK archive 可由宿主缓存上传，未提供缓存时才从 Adoptium 下载，并校验固定 SHA-256；
- JAR 放在 `/opt/haifa/haifa-agent.jar`；
- 配置放在 `/opt/haifa/haifa-eval.yaml`；
- JAR 和配置对 Agent 用户只读；
- 验证 JAR、配置 SHA-256；
- 执行 `java -jar ... --help` 并要求退出 0；
- Credential 不写入 Image、JAR 或配置。

## 4. 运行

逻辑命令：

```text
java -jar /opt/haifa/haifa-agent.jar
  --workspace <task-workspace>
  --config /opt/haifa/haifa-eval.yaml
  --approval auto
  --timeout PT20M
  --trace jsonl
  --trace-file /logs/agent/haifa-trace.jsonl
  -m <harbor-instruction>
```

要求：

- 使用非 root Agent 用户；
- Instruction 作为安全参数传递，不能直接拼 Shell；
- stdin 关闭；
- 不追加 Haifa 专用解题提示；
- 不使用 Terminal、Resume 或 `--verbose`；
- 每个 Trial 使用独立 Trace 和 SQLite；运行期间 SQLite/Transcript 位于 Container 本地临时文件系统，
  避免依赖宿主 bind mount 的文件锁语义；CLI 退出后必须把关闭的 SQLite 和 transcript JSONL 复制到
  `/logs/agent/`，并在删除 Container 前验证二者都非空；
- Candidate 非零退出不能阻止 Harbor Verifier。

## 5. Eval 配置

- 只注册本次使用的 DeepSeek 模型；
- Credential 为 `env://DEEPSEEK_API_KEY`；
- 只启用 `file.*` 和 `execution.run`；
- 禁用 MCP、Web 和外部 Skills；
- `approval.mode: auto`；
- `execution.provider: host-guarded`，外层 Container 是安全边界；
- 固定 50 Iterations、32 Tool Calls 和命令输出上限；
- SQLite 与 Transcript 在运行期间位于本 Trial 的 Container 本地 `/tmp`；CLI 退出后分别归档为
  `/logs/agent/haifa-runtime.db` 和 `/logs/agent/haifa-transcripts/`。安全 Trace 位于
  `/logs/agent/haifa-trace.jsonl`。

## 6. 输出

Adapter 只写入：

- CLI exit code；
- JAR/config digest；
- `/logs/agent/haifa-trace.jsonl`；
- CLI 关闭后的 `/logs/agent/haifa-runtime.db`；
- `/logs/agent/haifa-transcripts/` 下的安全 transcript JSONL。

stdout/stderr、started/finished/duration、timeout 和 Artifact 生命周期由 Harbor 记录，Adapter 不复制。

最终 Workspace 由 Harbor Verifier 读取。Adapter 不生成 `run-result.json`、`trial-result.json`，也不解析
自然语言回答。

## 7. 退出处理

| CLI 结果 | 处理 |
| --- | --- |
| 0 | 保存完成事实，继续 Verifier；Verifier 决定 PASS/FAIL |
| 1 | 若安装/配置错误则 ERROR；若任务机会内 Candidate 内部失败且 Verifier 可运行则 FAIL |
| 2 | 继续 Verifier，通常为 FAIL/timeout |
| Harbor/Container/Verifier 故障 | ERROR |

Adapter 必须区分 Candidate 错误与自身错误，不能把所有非零退出都抛成 Harbor 基础设施异常。

## 8. Timeout 与清理

- CLI 与 Harbor Agent timeout 都固定为 20 分钟，Verifier timeout 为 10 分钟；
- Adapter 把 timeout 交给 Harbor Environment，不实现第二套进程管理器；
- Harbor 删除每个 Trial 的独立 Container，从 Container 边界收敛 Java、Shell 和 Tool 子进程；
- Container 无法删除或 Verifier 无法运行时记 ERROR。

## 9. 最小 Contract Tests

1. JAR/Java/Manifest/`--help`；
2. Instruction 引号、换行、Unicode 和 Shell 元字符安全传递；
3. Workspace 显式且不越界；
4. Credential 不出现在 argv、日志和 Trace；
5. exit 0/1/2 都按规则保留 Verifier；
6. Adapter 不创建自有结果模型；
7. 真实 Trial 中检查 Trace JSONL 与 Container 清理。

## 10. MVP 不支持

- Windows Container、TUI、PTY；
- Resume、多步会话和 HITL；
- Web、MCP、外部 Skills；
- Git Push、PR 和宿主凭据；
- ATIF Trajectory；
- 多 Agent 或远程 Haifa Runtime。

## 11. 参考

- [Harbor Agents](https://www.harborframework.com/docs/agents)
- [Harbor Task Structure](https://www.harborframework.com/docs/tasks)
- `haifa-agent-applications/haifa-agent-cli/README.md`
- `haifa-agent-applications/haifa-agent-cli/src/main/java/io/haifa/agent/cli/HaifaCliMain.java`
