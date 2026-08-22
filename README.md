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
uv run evals admit --config evals/coding-smoke-v1.yaml \
  --tasks-path work/derived-tasks \
  --oracle-job-dir work/calibration/oracle \
  --nop-job-dir work/calibration/nop \
  --output work/admissions/coding-smoke-v1.json
uv run evals doctor --config evals/coding-smoke-v1.yaml \
  --admission work/admissions/coding-smoke-v1.json
uv run evals run --config evals/coding-smoke-v1.yaml
uv run evals collect --config evals/coding-smoke-v1.yaml \
  --job-dir work/coding-smoke-v1/<run-id> \
  --output reports/coding-smoke-v1/<run-id>/results.csv
uv run evals report --results reports/coding-smoke-v1/results.csv
uv run evals finalize --config evals/coding-smoke-v1.yaml \
  --job-dir work/coding-smoke-v1/<run-id> \
  --archive-dir run_data/coding-smoke-v1/final/<run-id>
uv run evals image seed-aider --container <stopped-aider-trial-container-id>
uv run evals image build --java-archive /path/to/OpenJDK21U-jdk_x64_linux_hotspot_21.0.8_9.tar.gz
uv run evals image check
uv run evals image prepare-tasks --config evals/coding-smoke-v1.yaml --tasks-path work/derived-tasks
uv run evals infra proxy start --source-port 2081
uv run evals infra check --output work/preflight/harbor-compose-network.json
uv run pytest
```

`admit` 在真实模型调用前核对冻结 Dataset/Task digest，并要求每个 Task 都有唯一的 Oracle PASS
和 NOP 可信 FAIL。准入 JSON 同时保留当前无法自动取得的 Verifier 测试数量，以及题面契约、测试全集和
离线依赖三项人工复核重点；它不修改 Harbor Reward，也不替代 Harbor Verifier。

`results.csv` 的 `status` 始终是 Harbor 官方 PASS/FAIL/ERROR；`trial_validity`、
`agent_clean_exit`、`failure_stage` 等字段是正交诊断维度，只解释运行是否有效及失败发生在哪一层，
不得反向改写正式成绩。

`doctor` 只做只读检查：准入证据、Dataset/Task digest、Harbor 版本、容器连接、JAR smoke、
Credential 变量存在性、磁盘和 Python 版本。配置 Harbor Compose overlay 时，它还要求一份未过期且
与 overlay、代理端点和容器后端完全匹配的真实 Compose 网络预检证据。它不会打印 Credential 值，
也不会下载镜像或调用模型。
非 `--plan-only` 的 `run` 会自动执行同一门禁；任一必需项失败时返回 2，不启动 Harbor。

默认运行目录是 `work/<eval-id>/<UTC-time>-<random>/`。每次运行同时生成唯一的
`*-run-manifest.json`，记录计划矩阵和配置/Dataset/Task/JAR/准入/preflight 摘要；已存在的 Run ID
不会被覆盖。正式收集应始终传入 `--config`，缺失、重复或未知 Candidate × Task × Attempt 会阻止
CSV 生成。

`finalize` 不覆盖已有目录。它把完整 Harbor Job 复制到私有 `jobs/harbor-job/`，在副本上执行矩阵与
Haifa SQLite/Trace/Transcript 校验，生成 `reports/results.csv`、`reports/comparison.md`、
`integrity/finalization.json` 和 `integrity/SHA256SUMS.txt`。缺失轨迹、无效 Trial、Credential/
reasoning/Provider 原始响应扫描命中或已存在归档目录都会阻止正式完成；诊断报告仍会保留，便于修复。

Collector 会从能够可靠识别的 Rust、Pytest、Jest 和 Gradle Verifier 摘要提取
Selected/Discovered/Ignored 数量。无法识别时保持 unknown，不猜测测试覆盖率。`execution.run` 可能产生
任意副作用，因此只有成功的结构化 `file.create/write/delete/move/patch` 才能直接证明工作区已修改；
只有命令执行证据时保持 unknown。

## Agent 基础设施镜像

`infra/agent-base/Dockerfile` 定义了不含题目和凭据的本地基础设施镜像，固定包含：

- `buildpack-deps:jammy` 的 OCI digest；
- Temurin JDK `21.0.8+9` 及压缩包 SHA-256；
- Aider `0.86.2`、Python `3.12.8` 和完整的 Python 依赖版本集合。

`image seed-aider` 只从一个已验证的 Aider trial 容器复制固定 Python 运行时与
`aider-chat` 安装目录，不复制题目、聊天历史、日志或模型输出。镜像构建会按规范化包名把实际安装元数据与仓库中的完整依赖锁逐项比较，因此来源容器不能静默带入另一套依赖。

构建命令把大体积 JDK 和 Aider runtime 复制到被忽略的 `work/image-cache/` 上下文，不把它们提交到 Git，也不需要容器访问外网。构建完成后会执行无模型冒烟检查，并把实际 image ID、大小、标签和 RepoDigests 写入
`work/image-cache/agent-infra/image-lock.json`。默认镜像名为
`localhost/haifa-agent-evals/agent-infra:jammy-jdk21-aider0.86.2-offline-deps-v4`。镜像还固定
Gradle 8.7 Wrapper 分发包；构建前必须把官方 zip 放在
`work/image-cache/gradle/gradle-8.7-bin.zip`（SHA-256
`544c35d6bd849ae8a5ed0bcea39ba677dc40f49df7d1835561582da2009b961d`），避免 Java verifier
在每个临时容器内重复下载。
构建脚本还要求 `work/image-cache/gradle/caches/modules-2/` 已预热 JUnit 5.10.0、
AssertJ 3.25.1 及其运行期依赖；只把 Gradle 的依赖制品/元数据缓存复制进镜像，不复制 daemon、
项目编译缓存、锁文件或运行日志。依赖缓存内容摘要记录在镜像 label 和 image lock 中。

v4 同时要求：

- `work/image-cache/python/wheels/` 中存在冻结 Python wheelhouse；
- `work/image-cache/cargo/cache-manifest.json` 与 `registry/cache/` 中实际 `.crate` 数量一致；当前 30 题
  的 Rust 子集没有外部 crate，因此允许明确记录为 0，而不是伪造缓存；
- Gradle、wheelhouse 与 Cargo cache 的 tree digest 全部写入 image label 与 image lock。

生成 Task 镜像后，Python 使用 `PIP_NO_INDEX=1` 和固定 wheelhouse，Cargo 使用
`CARGO_NET_OFFLINE=true`，Gradle Wrapper 被包装为始终追加 `--offline`。缺少依赖会立即失败，不再等待
公网超时。任何缓存或离线策略变化都会生成新的 Task/Dataset digest，不能混入历史基线。

Haifa adapter 会先探测镜像中的 Java 21，再决定是否上传 JDK；固定版 Aider adapter 会先核对精确版本，再决定是否联网安装。因此没有使用该镜像时仍可回退安装，使用后则不会在每个 trial 重复下载这两部分基础设施。

该镜像不能直接替代某一道 Harbor 题目镜像，因为它刻意不包含 `/app` 题目 workspace。若后续把它作为题目 Dockerfile 的基础层或生成 Harbor `docker_image`，必须固定新的环境/数据集摘要；不能把结果混入当前冻结数据集。

`image prepare-tasks` 完成上述转换：它先验证原始冻结数据集，并按 `/app` 文件内容匹配本机已经成功构建的 Harbor 题目镜像；随后从这些镜像复用语言工具链与 workspace，再叠加固定 Agent 基础设施，全程不重新下载语言依赖。生成镜像使用 RepoDigest 写入新的 `task.toml`，最后生成新的任务摘要、数据集摘要和 eval 配置。输出默认位于
`work/image-cache/task-environments/coding-smoke-v1-agent-infra-v4/`。

运行生成环境时显式指定两项本地证据：

```powershell
$root = "work/image-cache/task-environments/coding-smoke-v1-agent-infra-v4"
$env:HAIFA_EVAL_TASKS_PATH = "$root/tasks"
$env:HAIFA_EVAL_DATASET_MANIFEST_PATH = "$root/coding-smoke-v1-agent-infra-v4.dataset.toml"
uv run evals run --config "$root/coding-smoke-v1-agent-infra-v4.yaml"
```

## Podman 代理与真实 Harbor 网络预检

Windows + Podman Machine 的标准链路是：

```text
Windows proxy 127.0.0.1:2081
  -> SSH reverse tunnel, VM 127.0.0.1:1082
  -> VM TCP relay, 0.0.0.0:22081
  -> Harbor Compose main, host.containers.internal:22081
```

统一入口会先检查三段端口。发现完整健康链路时会复用并标记为外部管理，不启动重复 tunnel；发现只有
一部分端口被占用时会拒绝启动，避免覆盖未知进程：

```powershell
uv run evals infra proxy start --source-port 2081
$env:HAIFA_EVALS_CONTAINER_PROXY = "http://host.containers.internal:22081"
uv run evals infra check --output work/preflight/harbor-compose-network.json
```

`infra check` 使用 `infra/preflight/tasks/harbor-compose-network` 启动一条零模型 Oracle Trial，在 Harbor
实际生成的 Compose `main` 服务内经代理访问健康目标；普通 `podman run` 成功不能替代这份证据。
证据默认 30 分钟过期。正式运行应固定使用同一 overlay 和证据：

```powershell
$env:HAIFA_EVAL_EXTRA_DOCKER_COMPOSE = (Resolve-Path "infra/harbor-compose-proxy.yaml").Path
$env:HAIFA_EVAL_INFRA_EVIDENCE = (Resolve-Path "work/preflight/harbor-compose-network.json").Path
uv run evals doctor --config <eval.yaml> --tasks-path <tasks> --admission <admission.json> --container-cli podman
uv run evals run --config <eval.yaml> --tasks-path <tasks> --admission <admission.json> --container-cli podman
```

当前 Harbor 0.20 在 Windows Podman compatibility wrapper 下启用 phase `network_mode=no-network` 时，
平台探测会在环境启动前失败。因此 v4 已保证依赖客户端离线和依赖缓存完整，但尚未声称强制阻断容器
全部直接 egress；升级或修复 Harbor/Podman 兼容后应再启用 verifier phase 的网络隔离。

这样 Harbor 会使用每道题的预构建 RepoDigest，跳过 Dockerfile 构建；Haifa 与 Aider adapter 随后分别验证 Java 21 和 Aider 0.86.2 并跳过大体积安装。

每个 Haifa Trial 都启用 `SQLITE_WITH_JSONL`。运行期间 SQLite 和 transcript JSONL 写入 Container
本地 `/tmp`，CLI 退出并关闭数据库后，Adapter 会把以下复盘证据归档到 Harbor Trial 的 `agent/`
目录；任一轨迹缺失或为空都会使 Trial 明确失败，避免容器删除后得到无法复盘的成绩：

- `haifa-runtime.db`：SQLite 权威运行数据；
- `haifa-transcripts/*.jsonl`：持久化 transcript 投影；
- `haifa-trace.jsonl`：CLI 安全事件轨迹。

这些文件可能包含题目、模型输出和工具参数，只能作为私有运行数据保存；公开报告前必须完成凭据、
推理内容、Provider 原始响应和宿主路径扫描。

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
