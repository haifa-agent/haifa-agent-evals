# 第一次评测：Coding Smoke Comparison v1

> 状态：Ready for implementation
> Eval ID：`coding-smoke-v1`
> 日期：2026-08-19

## 1. 目的

用同一组 6 个多语言 Coding Tasks 运行 Haifa 和一个 Harbor 内置外部 Agent，验证：

- 两个 Candidate 都能执行；
- Verifier 结果能被统一收集；
- 能输出简单、可读的对比表。

这是工程 Smoke Comparison，不是正式排行榜或长期能力基线。

## 2. Dataset 与 Tasks

- Dataset：`aider/aider-polyglot`；
- 开发时固定当前已发布的精确版本和包摘要，禁止 `latest/main/head`；
- 从 C++、Go、Java、JavaScript、Python、Rust 各选择 1 个 Task；
- 选择规则：按 `SHA256(datasetVersion + taskId)` 排序，每种语言取第一个能通过离线校准的 Task；
- Task IDs 写入 `eval.yaml`，运行后不得更换。

离线校准只要求：

- Oracle/Reference 通过；
- no-op 失败；
- Verifier 能重复得到相同结果。

不为 MVP 建立完整 Task Quality Catalog，也不要求为每个 Task 制作多种错误解。

## 3. Candidates

### Haifa

- Agent ID：`haifa`；
- 入口：当前可执行 shaded CLI JAR；
- Java：21；
- 模式：one-shot `-m`；
- 模型：DeepSeek V4 Flash，对应配置中的冻结 API Style；
- 工具：`file.*` 与 `execution.run`；
- 禁用：Terminal、Resume、MCP、Web、外部 Skills、Git Push 和 GitHub；
- 时限：20 分钟；
- 审批：`auto`，stdin 关闭。

### 外部 Agent

- Agent ID：`aider`；
- 使用 Harbor 0.20.0 内置 Aider Agent；
- 优先使用与 Haifa 等价的 DeepSeek V4 Flash 模型和相同时限；
- 若 Harbor/Aider 无法使用同一模型，不静默换模型：在 `eval.yaml` 固定实际模型，并把结果明确标记为
  “完整系统对比”，不归因于 Agent 架构。

## 4. 执行规模

| 项目 | 数量 |
| --- | ---: |
| Tasks | 6 |
| Candidates | 2 |
| Attempts per Candidate/Task | 1 |
| 计划真实 Attempts | 12 |

开发阶段先用 Harbor `oracle` 和 `nop` 校准 6 个 Tasks，再执行 12 个真实 Attempts。真实模型调用前记录
模型、次数和总费用上限。

## 5. 输出

生成：

```text
reports/coding-smoke-v1/results.csv
reports/coding-smoke-v1/comparison.md
```

`results.csv` 每个 Attempt 一行；`comparison.md` 包含：

- Candidate 汇总；
- 6 个 Tasks 的逐项矩阵；
- FAIL/ERROR 原因；
- Candidate、模型、Dataset、Harbor 和 Haifa JAR 版本；
- “样本少、单次运行、不可作为排行榜”的限制声明。

## 6. 完成条件

1. 6 个 Tasks 的 Oracle 全部 PASS，nop 全部 FAIL；
2. Haifa 与 Aider 共 12 个计划 Attempts 全部产生 PASS、FAIL 或 ERROR；
3. 每个结果能回链 Harbor Trial；
4. Verifier Reward 与 CSV 一致；
5. Comparison 汇总与逐 Task 明细一致；
6. ERROR 不计入有效通过率分母，并单独显示；
7. Credential、reasoning、完整 Provider Response 和宿主路径零泄漏；
8. 报告不做统计显著性或优劣宣传。

## 7. 不做

- 不运行 30×3 或更多样本；
- 不建立长期 Baseline Series；
- 不引入私有/Fresh Dataset；
- 不自动分类复杂失败原因；
- 不把不同模型结果解释为纯 Agent 差异；
- 不因为结果不好修改 Task 选择。
