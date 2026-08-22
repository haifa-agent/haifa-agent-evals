# Haifa Agent Evals MVP 架构

> 状态：Ready for implementation
> 版本：v0.3
> 日期：2026-08-19

## 1. 目标

第一版只解决三件事：

1. 执行同一组 Coding Tasks；
2. 收集每个 Agent 的评分、耗时和错误；
3. 输出 Agent 对比结果。

最小链路：

```text
eval.yaml
  -> Harbor 执行 Candidate × Task
  -> Harbor Job/Trial 原始结果
  -> results.csv
  -> comparison.md
```

Harbor 负责 Task、Dataset、Agent、Container、Trial、Verifier 和原始结果。本仓不再开发通用 Harness、
Runner、Job/Trial Result、数据库或 Viewer。

## 2. 只保留四个概念

| 概念 | 含义 |
| --- | --- |
| Candidate | 一个可运行的 Agent + 模型 + 配置 |
| Task | Harbor Dataset 中的一个 Coding 任务 |
| Attempt | 一个 Candidate 对一个 Task 的一次独立执行 |
| Result | Verifier 分数、耗时、退出码和错误 |

不建立 Study Registry、Portfolio Catalog、Protocol Revision、Measurement Workbench、Finding 等领域层。
需要比较的内容直接写在一个版本化的 `eval.yaml` 中。

## 3. MVP 输入

`eval.yaml` 最少包含：

```yaml
id: coding-smoke-v1
dataset: aider/aider-polyglot@<exact-version>
tasks: [<six-frozen-task-ids>]
attempts: 1
timeoutMinutes: 20
candidates:
  - id: haifa
    agent: integrations.harbor.haifa_agent:HaifaCodingAgent
    model: deepseek-responses-flash
  - id: aider
    agent: aider
    model: <exact-harbor-model-route>
```

实现只校验当前字段。未知字段、浮动 Dataset 版本和重复 Candidate/Task 直接拒绝，不提前设计扩展 DSL。

## 4. 执行

- 一个 Attempt 对应一个 Harbor Trial；
- 每个 Attempt 使用全新 Container 和 Workspace；
- Candidate 使用相同 Task、时限和可见测试；
- Haifa 通过一个薄 `BaseInstalledAgent` Adapter 运行当前 CLI JAR；
- 外部 Candidate 优先使用 Harbor 内置 Agent；
- Verifier 是唯一正确性来源；
- 原始 Harbor Jobs 写入 `work/runs/`，不提交 Git。

Evals 入口只做两件事：把 `eval.yaml` 转为 Harbor Job Config，以及调用 Harbor。它不重新实现 Harbor 的
调度、超时、并发和恢复。

## 5. 数据收集

Collector 只读取 Harbor 原始结果，生成一个扁平 `results.csv`：

```text
eval_id,candidate,task_id,language,attempt,status,reward,duration_seconds,exit_code,error_type,trial_path
```

状态只有三种：

- `PASS`：Verifier 有效且通过；
- `FAIL`：Candidate 获得有效任务机会，但 Verifier 未通过，包括超时、无修改和 Candidate 内部失败；
- `ERROR`：Environment、Adapter 或 Verifier 无法产生可信评分。

不创建自有 JSON Result Schema。`results.csv` 是可从 Harbor Jobs 重建的分析表，不替代 Harbor 原始结果。

## 6. 对比输出

Reporter 从 `results.csv` 生成 `comparison.md`，至少包含：

| Candidate | Planned | Valid | Passed | Pass rate | Errors | Median duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |

并附：

- 每个 Task 的 Candidate 结果矩阵；
- FAIL/ERROR 明细；
- Candidate、Dataset、JAR、模型和 Harbor 版本；
- 本次结果的限制。

MVP 只有 6 个 Tasks、每项 1 次，结果只用于验证对比链路和发现明显差异，不计算复杂统计、不形成公开
排行榜结论。

## 7. 最小代码结构

```text
haifa-agent-evals/
  README.md
  pyproject.toml
  uv.lock
  evals/coding-smoke-v1.yaml
  src/haifa_agent_evals/
    cli.py
    collector.py
    reporter.py
    integrations/harbor/haifa_agent.py
  scripts/
    evals.py
    evals.ps1
    evals.sh
  tests/
  work/                 # README tracked; runtime contents ignored
    runs/               # evaluation, calibration and preflight jobs
    tasks/              # source, selected, derived and prepared tasks
    cache/              # image contexts and offline dependencies
    gates/              # admission and infrastructure evidence
    operations/         # reusable configs, controllers and helper scripts
    diagnostics/        # probes, verifier smoke jobs and reports
  reports/              # results.csv + comparison.md
  docs/
```

不按概念拆模块，不引入服务、数据库、消息队列和 Web UI。

## 8. 与 `haifa-agent-testing` 的关系

- Evals 负责运行公共任务并比较能力；
- Testing 负责确定性产品回归；
- Evals 的一个稳定失败可以人工提炼为小型回归测试；
- 两个仓库没有 Maven、Python 或运行时依赖；
- Benchmark、隐藏测试和评测产物不复制进 Testing。

## 9. MVP 完成标准

1. Haifa 和一个 Harbor 内置外部 Agent 能运行同一 6 个多语言 Tasks；
2. 每个 Attempt 都能关联 Harbor Trial 和 Verifier Reward；
3. 能生成字段稳定的 `results.csv`；
4. 能生成 Candidate 汇总和逐 Task 矩阵的 `comparison.md`；
5. PASS、FAIL、ERROR 不混淆；
6. 不泄漏 Credential、reasoning 和完整 Provider Response；
7. 不存在自研 Harness、Job Result、数据库、Dashboard 或插件平台。

## 10. MVP 后再考虑

只有最小闭环跑通后，才评估：

- 增加 Task 数和重复次数；
- 置信区间与成对统计；
- Fresh/Private Tasks；
- 更多 Agent、模型和 Benchmark；
- 自动失败分类；
- 趋势图和长期基线。

## 11. 配套文档

- [`02-first-study-charter.md`](02-first-study-charter.md)：第一次实际评测配置；
- [`03-evaluation-protocol-v1.md`](03-evaluation-protocol-v1.md)：最少公平、安全和错误规则；
- [`04-haifa-harbor-integration-contract.md`](04-haifa-harbor-integration-contract.md)：Haifa CLI Adapter；
- [`05-m0-implementation-and-acceptance-plan.md`](05-m0-implementation-and-acceptance-plan.md)：三阶段开发计划。
