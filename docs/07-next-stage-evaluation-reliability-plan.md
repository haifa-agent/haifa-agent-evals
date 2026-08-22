# 下一阶段：评测可靠性与可重复运行计划

> 状态：Proposed
> 日期：2026-08-20
> 前置：先完成 `coding-smoke-v1` 的 M0 Phase 2/3 与第一份真实对比报告

## 1. 目标

下一阶段不扩大为通用评测平台，只把当前最小链路变成可以可信地重复运行的工具：

```text
eval.yaml
  -> 运行前检查
  -> Harbor Candidate × Task
  -> 完整性校验
  -> results.csv
  -> comparison.md
```

需要解决的核心问题：

1. 在产生模型费用前发现 Container、Task、JAR、JRE、Credential 等环境问题；
2. 不因 Trial 缺失或重复而得到错误通过率；
3. 报告能说明本次究竟运行了什么版本；
4. 同一 Eval 可以再次运行，且不会覆盖或混入历史结果。

## 2. 先完成 M0 Phase 3

在开始 M1 前，主任务仍按
[`05-m0-implementation-and-acceptance-plan.md`](05-m0-implementation-and-acceptance-plan.md)
完成第一份真实结果。Phase 3 在现有计划上重点补齐：

- oracle 6 个 PASS、nop 6 个 FAIL，校准失败时禁止模型调用；
- Haifa 与 Aider 共 12 个真实 Attempts；
- 12 个 Candidate × Task 组合唯一、无缺失、无重复；
- 报告包含 Dataset、Harbor、Candidate、模型和 Haifa JAR/config 摘要；
- CSV 汇总、逐 Task 矩阵与 Harbor Reward 可相互复算；
- 发布范围通过 Credential、reasoning、Provider Response 和宿主绝对路径扫描；
- Container 删除后无残留 Java、Shell 或 Tool 子进程。

Phase 3 仍使用现有 `run -> collect -> report`，不在真实运行前引入新的抽象。

## 3. M1-A：结果完整性与运行证据

### 实现

- 扩充现有 `eval-plan.json`，记录本次运行的不可变事实：
  - Eval 配置摘要；
  - Dataset digest 与 Task IDs；
  - Candidate ID、Agent 和模型路由；
  - Haifa JAR/config digest；
  - Harbor、Python 和 Container Backend 版本；
  - started/finished 时间与运行目录。
- `run-manifest.json` 只保存运行溯源，不定义第二套 Trial/Result Schema；
- `collect` 同时读取 Eval 配置和 Harbor Jobs，校验预期矩阵；
- 缺失、重复、未知 Candidate/Task 直接使完整性门禁失败，不能静默缩小分母；
- Reporter 增加 Data quality 和 Reproducibility 两个简短区块。

### 测试

- 12 个预期组合全部存在时通过；
- 缺失、重复和意外组合分别失败；
- Manifest 不包含 Credential、Prompt、reasoning 或完整宿主路径；
- Report 的 Planned、Observed、Valid、Passed、Errors 可从 CSV 复算。

### Commit

`feat: validate evaluation completeness and provenance`

## 4. M1-B：运行前检查

新增一个小型只读入口：

```text
evals doctor --config evals/coding-smoke-v1.yaml
```

### 默认检查

- Eval 配置可解析且 Dataset 已固定；
- Harbor 版本与 lock 一致；
- Docker/Podman 和 Compose 可连接；
- 配置的 6 个 Task 可从 Registry 或本地缓存解析；
- Haifa JAR 存在、SHA-256 可计算、`--help` 可执行；
- Java 21 或固定 JRE archive 可用；
- Candidate 所需 Credential 已设置，但不读取或输出值；
- `work/` 可写且剩余磁盘满足最低要求。

默认不访问模型、不修改 Container 状态。确实需要拉取 Dataset、Image 或 JRE 时，使用显式
`--online`/`prepare` 动作，运行产物仍只写入忽略的 `work/`。

### 输出与退出

- 每项只输出 `PASS / FAIL / SKIP` 和脱敏原因；
- 任一强制项 FAIL 时禁止进入真实模型运行；
- doctor 失败属于运行前环境问题，不计入 Candidate 的 FAIL/ERROR。

### Commit

`feat: add evaluation preflight checks`

## 5. M1-C：可重复运行与一键收尾

### 运行目录

每次运行生成一个稳定 `run-id`：

```text
work/runs/evaluations/<eval-id>/<run-id>/
reports/<eval-id>/<run-id>/
```

- 不覆盖历史 Harbor Job；
- 同一目录只能接收同一配置指纹；
- 重跑基础设施 ERROR 时保留原 Trial，不选择最好结果；
- 不实现数据库、调度器或长期 Baseline Service。

### 一键收尾

在保留 `run`、`collect`、`report` 独立命令的同时，允许 `run --finalize` 在 Harbor 结束后依次执行：

1. 完整性校验；
2. CSV 收集；
3. Markdown 报告；
4. Secret/host-path scan；
5. Container 和残留进程检查。

Candidate 得到 FAIL 不应使收尾流程崩溃；环境或证据不完整必须返回非零退出码。

### Commit

`feat: finalize repeatable evaluation runs`

## 6. M1 完成标准

1. `doctor` 能在模型调用前识别主要环境阻塞；
2. 一次完整运行有唯一目录和可重建的运行指纹；
3. 缺失或重复 Trial 不可能生成“看似正常”的通过率；
4. `results.csv`、`comparison.md` 和 Harbor 原始 Reward 一致；
5. 报告包含必要版本和小样本限制，不包含秘密或宿主绝对路径；
6. 同一配置连续运行两次不会覆盖、串入或复用错误结果；
7. 仍然只有 Harbor 一个执行和评分引擎。

## 7. M1 暂不做

- 数据库、Web UI、Dashboard 和公开排行榜；
- Study/Portfolio/Registry、插件系统或第二套 Harness；
- 分布式 Worker、队列、定时调度和远程控制面；
- 自动复杂失败归因；
- 多 Dataset DSL、长期趋势和 Baseline Service；
- Token/Cost 比较，除非两个 Candidate 都能提供同口径可信数据；
- 置信区间、Bootstrap、pass@k 等统计能力。

## 8. 后续 M2 候选

只有 M1 能稳定重复执行后，才考虑：

- 将 Attempts 从固定 1 放开到 3；
- 新增独立的标准评测 YAML，而不是扩张当前 Smoke 配置；
- 扩大多语言、多文件修改、调试、测试修复和长任务 Task 覆盖；
- 增加成对通过率、错误率、耗时分位数和可比成本；
- 研究其他公开 Coding Benchmark，但每个 Dataset 仍使用精确版本和官方 Verifier。

## 9. 重点人工 Review/Test

- Doctor 是否只检查 Credential 存在而不读取/打印值；
- 本地 Task Cache 是否与冻结 Dataset/Task 身份一致；
- Haifa/Aider 是否得到相同 Task、时限、网络和模型条件；
- 非零 Agent exit 后 Harbor 是否仍执行 Verifier；
- 缺失/重复 Trial 是否会可靠阻止正式报告；
- 运行指纹是否足以从原始 Harbor Jobs 重建结果；
- Secret Scan 是否覆盖 Trace、stdout/stderr 和生成报告；
- timeout 后 Container 与 Java/Shell/Tool 进程是否真正收敛。
