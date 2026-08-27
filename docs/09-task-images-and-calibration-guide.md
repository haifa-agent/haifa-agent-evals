# 题目镜像、校准与准入操作指南

> 状态：工程运行指南  
> 日期：2026-08-26  
> 适用范围：`haifa-agent-evals` 使用 Harbor 执行本地派生题集和上游已验证题集

## 1. 目标与边界

本文说明评测题目如何从 Task 包变成可重复启动的容器环境，以及为什么在调用付费模型前要执行
Oracle/NOP 校准。目标不是增加另一套 Harness，而是把 Harbor 已有的 Task、Trial、Environment 和
Verifier 组织成可审计、可复现、fail-closed 的评测流程。

必须始终区分四件事：

- 题目是否可执行；
- Verifier 是否能区分“未实现”和“正确实现”；
- Candidate/Agent 是否获得了有效机会并正常结束；
- Harbor Verifier 是否最终给出 PASS。

校准只解决前两项，不调用待评模型，也不代表 Coding Agent 的能力成绩。

## 2. 核心对象

### 2.1 Harbor Task 包

典型本地 Task 包包含：

```text
task/
├── instruction.md       # 给 Candidate 的公开任务契约
├── task.toml            # Task 身份、超时、预构建镜像等元数据
├── environment/         # 初始 workspace 与容器构建定义
├── tests/               # Verifier 入口和测试
└── solution/            # 仅供 Oracle 校准使用的参考实现
```

`solution/` 不能暴露给正式 Candidate。正式评分时，Harbor Verifier 是唯一正确性事实源；Collector、
Agent stdout、退出码和自然语言结论都不能改写 Reward。

### 2.2 题目基础镜像

题目基础镜像提供语言工具链和初始 workspace，例如 `/app` 或 `/testbed`。它可能由 Task Dockerfile
构建，也可能由 `task.toml` 引用预构建 OCI 镜像。基础镜像不应包含 Candidate 凭据、模型响应或
Oracle 答案。

### 2.3 Agent 基础设施镜像

`infra/agent-base/Dockerfile` 构建与具体题目无关的 Haifa/Aider 基础设施镜像。它固定 Java、Aider、
Gradle 分发包、Python wheelhouse、Cargo cache 等离线依赖，并通过 image label 与
`image-lock.json` 记录摘要。该镜像刻意不包含题目 workspace，不能单独作为某道题的运行环境。

### 2.4 派生题目镜像

`evals image prepare-tasks` 将题目基础镜像和 Agent 基础设施镜像组合成最终可运行镜像：

```text
题目基础镜像：语言工具链 + workspace
                  │
                  ├── 叠加固定 Agent/JDK/离线依赖
                  ▼
派生题目镜像：最终 Trial 实际启动的环境
```

生成过程会：

1. 验证来源 Dataset Manifest 与全部 Task digest；
2. 按 workspace 内容找到唯一匹配的本机题目镜像；
3. 固定来源镜像和 Agent 基础设施镜像的 RepoDigest；
4. 生成新的 Task 包，并让 `task.toml` 引用预构建镜像；
5. 重新计算每个派生 Task digest、Dataset digest 和 eval 配置；
6. 把镜像清单写入 `images.json`。

派生 Task 是新的可执行评测输入，不能冒充来源 Task。只要 Dockerfile、离线依赖、网络策略或镜像
引用发生变化，就必须生成新的 Task/Dataset digest。

### 2.5 `prepare-tasks` 构建阶段具体在做什么

`image prepare-tasks` 不是在运行题目测试，也不调用 Oracle、NOP 或待评模型。它是在调用付费模型前，
把每道题的来源环境加工成可离线、可重复启动且由摘要固定的最终 Trial 环境。单道题依次执行：

1. 校验来源 `task.toml`、workspace 内容摘要和 Dataset Manifest，找到与该题 workspace 精确匹配的
   本机 Harbor 题目镜像；若不存在完整题目镜像，则回退到对应语言基础镜像，并把 Task 包中的
   `environment/workspace` 重新复制到 `/app`；
2. 生成多阶段 Dockerfile，以来源题目镜像保留语言工具链、初始代码和 Verifier 运行环境，再从固定
   Agent 基础设施镜像复制 Java 21、Aider 0.86.2、Python 3.12 和离线依赖缓存；
3. 对 Java 题复制 Gradle Wrapper 与模块缓存，并包装 `gradlew` 使其始终追加 `--offline`；设置
   `PIP_NO_INDEX=1`、本地 Python wheelhouse 和 `CARGO_NET_OFFLINE=true`，防止 Trial 临时访问公网；
4. 使用 `podman build --pull=never` 构建独立题目镜像。构建可以按批次并行，但每题输出身份仍由来源
   Task digest、来源镜像 RepoDigest 和 Agent 基础设施 RepoDigest 共同决定；
5. 构建成功后读取最终镜像 RepoDigest，把不可变镜像引用写入派生 `task.toml`，重新计算 Task digest；
6. 全部已准备题目完成后，生成新的 Dataset Manifest、Dataset digest、eval YAML 和 `images.json`。

可以把该过程理解为：

```text
来源题目镜像
= 语言工具链 + 初始 workspace + Verifier 运行环境

Agent 基础设施镜像
= Java 21 + Aider 0.86.2 + Python 3.12
  + Python wheels + Cargo cache + Gradle cache

派生题目镜像
= 来源题目环境 + 固定 Agent 运行时 + 离线依赖
```

OCI 公共层由容器存储复用，并不是每道题都重新联网下载完整工具链；但每道题仍有独立、可审计的最终
镜像引用。后续 Oracle、NOP 和正式 Candidate Trial 直接启动该预构建镜像，避免把依赖安装耗时、
公网波动或上游镜像漂移误判为 Agent 能力失败。

因此阶段边界必须写清楚：`prepare-tasks` 证明的是“最终环境已构建并固定”，下一步 Oracle/NOP 校准
才验证“正确实现能通过、空实现不能通过”；两者都不能替代正式模型评测。

## 3. Trial 与容器模型

一次 Trial 对应一个 Candidate/Task/Attempt 组合。对本轮 Aider Polyglot 单服务题：

- 一个 Oracle Trial 使用一个全新的 `env-main` 容器；
- 一个 NOP Trial 使用另一个全新的 `env-main` 容器；
- Oracle/NOP、Verifier 和 workspace 操作发生在各自 Trial 的同一个容器中；
- Harbor 控制器运行在主机，不额外占用“题目容器”；
- Task 若显式声明数据库等 sidecar，一次 Trial 才可能启动多个容器。

因此一套 120 题的 Oracle/NOP 校准共有 240 个 Trial，但通常不是 360 个容器。

## 4. Oracle/NOP 校准

### 4.1 NOP 基线

NOP 不修改初始代码，直接执行 Verifier。可信结果必须满足：

```text
Verifier 确实执行
AND Reward 存在
AND Reward < 1
```

NOP PASS 表示题目初始状态已经通过，或 Verifier 覆盖不足。此类题不能进入
`PER_TASK_CALIBRATED` 正式题集，必须修复 Verifier 或使用同语言、同选择规则的候补题替换。

### 4.2 Oracle 校准

Oracle 在独立容器中应用 Task 自带参考实现，再执行同一套 Verifier。可信结果必须满足：

```text
Verifier 确实执行
AND Reward 存在
AND Reward >= 1
```

Oracle FAIL 不能立即解释为题目错误。必须先区分：

- 依赖下载、镜像拉取或 DNS/代理失败；
- Environment/Verifier timeout；
- Verifier 没有写 Reward；
- 参考实现与公开契约或测试不一致；
- Task 包本身损坏。

只有排除基础设施故障后，Oracle 仍无法通过，才应将题目标记为不合格。

### 4.3 成对准入

同一道题只有在 Oracle 与 NOP 都完成且满足上述条件时才是一个完整校准对。不得用“Oracle 通过但
NOP 未运行”或“前一 Job 的单边结果”补足准入。跨 Job 合并只允许按 Task ID 选择完整、唯一、可信
的 Oracle/NOP 对，并保留全部原 Job，不得挑最好的一次覆盖失败证据。

## 5. 两种 Dataset 信任模式

### 5.1 `PER_TASK_CALIBRATED`

本地挑选或派生的 Aider Polyglot 题集使用逐题校准：

```text
固定来源 Task/Dataset digest
        ↓
准备最终实际运行的题目镜像与派生 digest
        ↓
对最终 Task 集执行 Oracle/NOP
        ↓
生成 admission.json
        ↓
doctor → 正式评测
```

准入要求每题结构完整、Task digest 匹配、Oracle PASS、NOP FAIL。若正式 Trial 使用的是派生 Task，
准入证据也必须绑定派生后的最终 Task/Dataset digest；不能只校准来源 Task 后静默改变执行环境。

### 5.2 `UPSTREAM_VERIFIED`

SWE-bench Verified 等受信上游题集使用仓库内受控策略：固定 Registry Dataset/Task digest，并引用
上游 Benchmark、Harbor Adapter 与 Parity 证据。该模式不伪造本地 Oracle/NOP 结果，但仍要冻结本机
可启动镜像并执行小规模基础设施 Smoke。当前受信列表以 `admission.py` 为事实源。

## 6. 推荐执行顺序

### 6.1 选择与冻结题集

1. 固定上游 Dataset digest；
2. 显式枚举 Task ID；
3. 使用不依赖难度、Oracle 答案或历史模型成绩的确定性规则选题；
4. 排除历史集合时按精确 Task ID 比较；
5. 生成本地 Dataset Manifest，并验证 Task 内容摘要；
6. 为每种语言保留同规则排序的候补队列。

### 6.2 准备最终镜像

1. 先拉取或构建题目基础镜像；
2. 准备并校验 Agent 基础设施镜像及离线依赖；
3. 运行 `image prepare-tasks` 或对应的环境冻结入口；
4. 固定生成镜像的 RepoDigest；
5. 生成最终 Task/Dataset digest；
6. 对最终实际运行的 Task 集执行校准。

对于冷启动成本很高的题目，可以先做来源环境探测来发现 Verifier 缺陷，但它不能替代最终派生环境
的准入校准。

### 6.3 生成准入和预检

```powershell
uv run evals admit `
  --config <final-eval.yaml> `
  --tasks-path <final-tasks> `
  --oracle-job-dir <calibration-job> `
  --nop-job-dir <calibration-job> `
  --output <admission.json>

uv run evals doctor `
  --config <final-eval.yaml> `
  --tasks-path <final-tasks> `
  --admission <admission.json> `
  --container-cli podman
```

同一个 Harbor Job 可以同时包含 Oracle 和 NOP，`admit` 会按 `agent_info.name` 分别提取；每种 Agent
每道题必须恰好一个结果，重复结果会 fail-closed。

## 7. 依赖预热与离线运行

把依赖下载留在每个临时 Trial 中会同时损害速度和可信性：网络超时可能让 Oracle 得 0，进而被误判
为参考答案失败。应优先在受控构建阶段完成下载、校验摘要，并在正式 Trial 中离线使用。

当前 Agent 基础设施镜像固定：

- Gradle 8.7 Wrapper zip，SHA-256 为
  `544c35d6bd849ae8a5ed0bcea39ba677dc40f49df7d1835561582da2009b961d`；
- Gradle 依赖缓存；
- Python wheelhouse；
- Cargo registry cache；
- Java 21 和 Aider 固定版本。

生成 Task 镜像后，Python、Cargo、Gradle 应处于离线/fail-fast 模式。缺失依赖应明确失败，而不是在
Verifier timeout 内长时间等待公网。

## 8. 并发、资源与磁盘门禁

评测 YAML 的 `concurrency` 默认是 1，允许 1 到 16；Runner 将其写入 Harbor
`n_concurrent_trials` 和 Run Manifest。并发度变更不能热应用，必须启动新的 Harbor Job，因此新 Job
计数会从 0 开始；旧 Job 结果和镜像缓存不会自动删除。

建议：

- 付费 Candidate 评测从并发 1～2 开始；
- Oracle/NOP 校准可在资源允许时提高并发；
- 同时观察 CPU、可用内存、容器数量、超时/错误增长和 D 盘可用空间；
- 浏览器、IDE 等宿主大型程序的资源占用必须与容器占用分开判断；
- 不因单次 CPU 峰值判定过载，优先看持续内存压力、OOM、timeout 和错误率。

磁盘采用硬门禁：

```text
D 盘可用空间 <= 50 GB
    → 停止拉取或构建新的题目镜像
    → 不再扩充到计划题数
    → 只保留已经完成 Oracle/NOP 成对校准且已完成环境冻结的题目
    → 重新生成实际题量与语言分布的最终配置和 Manifest
```

门禁不能把“已经下载 Task 包”误写成“已经冻结可运行镜像”。`image prepare-tasks` 的
`--minimum-free-gb` 会在开始下一道题前检查输出盘，并在触发后只用已经完整冻结的题目生成部分
Dataset Manifest、eval 配置和 `images.json`；外层运行编排仍应定期观察磁盘，不能发布未完成 staging。
镜像构建并发由独立的 `--build-concurrency` 控制，不等于正式 Trial 的 `concurrency`。并发构建按批次
提交，门禁触发后不提交下一批，但允许当前批次完整结束，避免发布半个 Task 镜像。

## 9. 失败分类

| 现象 | 分类 | 处理 |
| --- | --- | --- |
| Oracle=1、NOP=0 | 可信校准对 | 可准入 |
| NOP=1 | `VERIFIER_UNDERCOVERED` | 修复 Verifier 或换候补题 |
| Oracle=0，日志为依赖下载超时 | 基础设施 ERROR | 预热依赖后重校，不判题目失败 |
| `VerifierTimeoutError` | 基础设施或 Verifier ERROR | 保留原 Trial，查进程/网络/测试耗时 |
| Verifier 执行但无 Reward | Verifier 协议 ERROR | fail-closed 修复 Reward 收尾逻辑 |
| Oracle 稳定为 0 且环境正常 | 题目/Oracle/契约不一致 | 人工审查后剔除或发布新 Task 版本 |
| Candidate 非零退出但 Verifier 可运行 | 正式评测 FAIL | 不自动改写为基础设施 ERROR |
| 容器或 Verifier 无法产生可信评分 | 正式评测 ERROR | 仅暂态基础设施故障允许一次重试 |

Verifier 的 fail-closed 修复只能保证失败路径写出 `reward=0`，不能修改测试断言、公开契约或通过标准。
修复后 Task digest 必须更新并重新校准。

## 10. 证据与可审计性

每次校准和正式评测应保留：

- eval YAML 与摘要；
- Dataset Manifest、Task digest 和镜像 RepoDigest；
- Harbor Job 配置、每个 Trial 的 `config.json` 与 `result.json`；
- Verifier stdout/stderr 和 Reward；
- admission、doctor/preflight 和 Run Manifest；
- 正式评测的 SQLite、Trace、Transcript 与最终归档完整性摘要。

必须分别报告：Planned、Valid、PASS、FAIL、ERROR、Agent clean exit、Verifier 是否执行、证据是否完整。
不得输出 Credential、完整 Prompt、供应商原始响应或模型私有推理。

## 11. 运行经验示例

一次 120 题 Polyglot 校准中，并发 6 最初因宿主 IDE/浏览器占用导致内存余量很低；关闭无关大型
程序后可用内存显著恢复。校准还发现三道 Go 题 NOP 也能通过，证明校准的价值不仅是“跑通容器”，
还包括识别弱 Verifier。

同一次运行的 Java 题在每个隔离容器内重复下载 Gradle 8.7，导致 Oracle 得 0 和
`VerifierTimeoutError`。日志中的 `SocketTimeoutException: Read timed out` 证明这是依赖供应链故障，
不能归因于参考实现。正确处理是固定官方 zip 摘要、把依赖放入基础设施/派生题目镜像，再对受影响的
最终 Task 重校，而不是降低 Oracle 准入标准。

这些经验是通用的故障分类方法，不构成针对某个模型或某套题目的提分规则。

## 12. 相关文档

- `docs/03-evaluation-protocol-v1.md`
- `docs/04-haifa-harbor-integration-contract.md`
- `docs/06-evaluation-infrastructure-improvement-priorities.md`
- `docs/08-support-swe-bench.md`
- `README.md` 的 Agent 基础设施镜像与运行命令章节
