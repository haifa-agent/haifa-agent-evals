# Haifa Agent Evals

一个独立、轻量的 Coding 评测运行器。评测执行与判分由 Harbor 负责，本仓库只做：

1) 校验一份固定的 `eval.yaml` 配置；
2) 触发 Harbor Job；
3) 收集结果到扁平 `CSV`；
4) 输出可读的 Markdown 对比报告。

## 题目来源与开源声明（必须清楚）

本仓库当前使用的评测题目是：

- 数据集基线：`aider/aider-polyglot@sha256:01e28d85e46beae5b7e29a29f57cb49d882b5486583d52cec4ee5bf3540a1c84`（外部公开开源数据集）
- 当前运行配置使用的冻结数据集：`haifa/coding-smoke-v1-cpp-verifier-fixed@sha256:80302f17fa66fc5f08a72339635672594eaed941e6738aca627fa472dab52a79`

`coding-smoke-v1` 的 6 道题分两类：

- 其中 5 道直接沿用上游开源任务 ID；
- 其中 1 道（`haifa/coding-smoke-cpp-gigasecond`）是基于上游 `aider-polyglot` 的本地衍生题，做了可复现兼容修订：
  - 调整了包名；
  - 补了 2 处 verifier 提前退出场景；
  - 变更记录在 `evals/patches/aider-polyglot-cpp-verifier-reward.patch`；
  - 衍生清单在 `evals/coding-smoke-v1.dataset.toml`。

该衍生数据集没有发布到 Harbor Registry；它是仓库内版本化文件（`evals/*.dataset.toml` + `evals/patches/*`）用于审计复现。

换言之：**不是纯自建题库，也不是“黑箱题库”；是“公开开源基线 + 明确记录的本地派生修订”**。

## 环境要求

- Python 3.12
- `uv`
- Docker，或可兼容 Docker 的 Podman（用于真实 Harbor 试跑）
- 当前可用的 Haifa CLI shaded JAR（用于 Haifa candidate）

## 运行命令

```bash
uv sync --frozen
uv run evals run --config evals/coding-smoke-v1.yaml
uv run evals collect --job-dir work/coding-smoke-v1 --output reports/coding-smoke-v1/results.csv
uv run evals report --results reports/coding-smoke-v1/results.csv
uv run pytest
```

## 配置与执行约束

`evals/coding-smoke-v1.yaml` 已固定：

- Harbor 版本：`0.20.0`
- 6 个任务 ID
- 两个 model route（Haifa 与 Aider）
- 本地派生数据清单：`evals/coding-smoke-v1.dataset.toml`

默认 `run` 期望在 `work/derived-tasks` 下有对应的 6 个 task 目录，并在执行前校验 Harbor task digest 与 dataset digest；如果要使用其他完整缓存目录，请用 `HAIFA_EVAL_TASKS_PATH` 指向该路径，校验逻辑同样生效。

可选环境变量：

- `HAIFA_EVAL_JAR_PATH`：覆盖默认 Haifa JAR 位置；
- `HAIFA_EVAL_JAVA_ARCHIVE_PATH`：使用固定的 Temurin JDK 压缩包（否则缺失时按需下载并校验）；
- `HAIFA_EVAL_EXTRA_DOCKER_COMPOSE`：仅在本地环境确实需要额外 Harbor compose 覆盖层时使用，路径必须已存在，Runner 会写入生成的计划与 Job 配置。

`work/` 下运行产物和 `reports/` 报告文件按仓库约定写入并默认忽略（不提交）。

## 参考

- [`docs/01-haifa-agent-evals-architecture.md`](docs/01-haifa-agent-evals-architecture.md)
- [`docs/05-m0-implementation-and-acceptance-plan.md`](docs/05-m0-implementation-and-acceptance-plan.md)
