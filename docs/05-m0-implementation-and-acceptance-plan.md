# M0 开发与验收计划（简化版）

> 状态：Ready for implementation
> 日期：2026-08-19

## 1. 交付目标

M0 完成后能够执行：

```text
evals run --config evals/coding-smoke-v1.yaml
evals collect --job-dir work/<job>
evals report --results reports/coding-smoke-v1/results.csv
```

得到：

```text
reports/coding-smoke-v1/results.csv
reports/coding-smoke-v1/comparison.md
```

## 2. 开发原则

- 只开发执行入口、Haifa Adapter、Collector 和 Reporter；
- 直接复用 Harbor，不开发通用 Harness；
- 只支持一个 `eval.yaml` 结构和一个 Dataset；
- Python 3.12，Harbor 0.20.0，使用 `uv.lock`；
- `.ps1/.sh` 只透传 Unix 风格参数；
- 每阶段完成后自审、修复、测试并提交，再进入下一阶段；
- 不修改 `haifa-agent` 主仓；发现产品缺口单独报告；
- 不提交 work、Harbor Jobs、Trace、SQLite、Workspace 和 Credential。

## 3. Phase 1：仓库与结果结构

### 实现

- 初始化独立 Git 仓库和 `feat-m0-evals` 分支；
- 创建 README、AGENTS、pyproject、uv.lock 和 `.gitignore`；
- 创建 `evals/coding-smoke-v1.yaml`；
- 实现最小 CLI：`run`、`collect`、`report`；
- 先用固定 Fake Harbor Results 实现 `results.csv` 和 `comparison.md`；
- 添加 `.ps1/.sh` 包装器。

### 测试

- 未固定 Dataset 版本、重复 Candidate/Task、未知字段被拒绝；
- Collector 正确生成每 Attempt 一行；
- Reporter 正确计算 Valid、Passed、Pass rate、Errors 和中位耗时；
- PASS/FAIL/ERROR 分母正确；
- 包装器参数原样透传；
- 报告不包含秘密字段。

### 自审删除项

- 删除通用 Study/Portfolio/Protocol 类；
- 删除自有 Job/Trial Result Schema；
- 删除数据库、插件、Pipeline 和未来扩展点。

### Commit

`feat: add minimal evaluation result pipeline`

## 4. Phase 2：Harbor 与 Haifa Adapter

### 实现

- 固定 Harbor 0.20.0；
- 接入 `aider/aider-polyglot` 精确版本；
- 按固定规则选择 6 个语言 Tasks；
- 实现 Haifa `BaseInstalledAgent`；
- 创建无密钥 `haifa-eval.yaml`；
- 用 Harbor oracle/nop 校准 6 个 Tasks；
- 把真实 Harbor Job 接到 Collector。

### 测试

- 6 个 Oracle 全 PASS、nop 全 FAIL；
- 当前 Haifa JAR `--help` 在 Container 内退出 0；
- Instruction 安全传递；
- exit 0/1/2 不阻止 Verifier；
- Credential 不泄漏；
- Trace 为合法 JSONL；
- timeout 后进程收敛；
- Harbor Reward 与 CSV 一致。

### 自审删除项

- Adapter 只负责 install/run/context；
- 不解析 stdout 评分；
- 不复制 Harbor Artifact/Viewer/Result；
- 不加入第二个执行引擎。

### Commit

`feat: run haifa coding agent in harbor`

## 5. Phase 3：真实对比与报告

### 前置

- Phase 1/2 测试全部通过；
- Haifa 与 Aider Candidate 配置、模型和 JAR 摘要已固定；
- 12 个真实 Attempts 的费用上限已记录；
- Credential 仅通过环境注入。

### 执行

- Haifa 运行 6 个 Tasks，各 1 次；
- Aider 运行同一 6 个 Tasks，各 1 次；
- 失败不自动重试；ERROR 仅允许一次基础设施重试；
- 收集 Harbor Reward、duration、exit 和 error；
- 生成 `results.csv` 与 `comparison.md`；
- 执行 Secret Scan 和残留进程检查。

### 测试与核对

- 12 个计划 Attempts 都有 PASS、FAIL 或 ERROR；
- CSV 行数、Candidate/Task 组合和 Trial 路径完整；
- 汇总值能从 CSV 手工复算；
- 逐 Task 矩阵与 CSV 一致；
- ERROR 单列且不进入有效通过率；
- 报告包含版本、模型差异和小样本限制；
- 日志和报告无 Credential、reasoning、完整 Provider Response 和 Host Path。

### Commit

`feat: produce the first coding agent comparison`

## 6. M0 完成标准

1. 一条命令能启动配置中的全部 Candidate × Task；
2. Harbor 原始结果可重建 `results.csv`；
3. `comparison.md` 有 Candidate 汇总和逐 Task 矩阵；
4. PASS、FAIL、ERROR 规则经过测试；
5. Haifa 和 Aider 完成同一 6 个 Tasks；
6. 失败、超时和基础设施错误都可见；
7. 没有自研 Harness、数据库、Dashboard、Job Result 或插件平台；
8. 每个 Phase 已自审、修复并提交；
9. `haifa-agent` 主仓无修改；
10. 最终报告明确这只是 Smoke Comparison。

## 7. 重点人工 Review/Test

最终报告列出并建议人工检查：

- Haifa 是否确实在非 root Container 中工作；
- 非零 CLI exit 后 Harbor 是否仍运行 Verifier；
- Haifa/Aider 模型和网络条件是否真的可比；
- 6 个 Task 的 Oracle/no-op 是否正确；
- CSV 与 Harbor 原始 Reward 是否一致；
- comparison 汇总是否能手工复算；
- Candidate 是否能看到隐藏测试或搜索 Benchmark 答案；
- Trace 和日志是否泄漏秘密；
- 超时后是否残留 Java/Shell/Tool 进程。

## 8. 停止条件

- 无法固定 Dataset 版本或许可证；
- Aider 无法使用已声明模型且未明确改为系统对比；
- Candidate 非零退出会导致 Verifier 被跳过；
- Hidden Tests/Gold Patch 对 Candidate 可见；
- Credential 泄漏或进程无法收敛；
- 需要修改 Haifa 产品 API 才能继续。

最后一种情况单独回到 `haifa-agent` 立项，不在 Evals 内复制产品实现。
