查清楚了。结合 Harbor 当前实现和你现有 `haifa-agent-evals`，结论比预想更好：

> **Haifa 接 SWE-bench Verified 基本不需要新增 Agent/Runner/Verifier，只需要增加 Dataset 接入和针对 SWE-bench 的评测策略。**

而且我发现一个很关键的事实：Harbor 的 SWE-bench Verified 不是“兼容实现”，它在 verifier 里直接调用 SWE-bench 官方 harness 的 `make_test_spec()`、`get_logs_eval()`、`get_eval_tests_report()`、`get_resolution_status()`。

### 1. Harbor 已经把 SWE-bench Verified 做成一等 Dataset

现在 Harbor Hub 有正式公开数据集：

```text
swe-bench/swe-bench-verified
500 tasks
```

可以直接：

```bash
harbor run \
  -d swe-bench/swe-bench-verified \
  -a <agent> \
  -m <model>
```

Harbor 官方文档就是这么推荐的。([Harbor Hub][1])

所以对 Haifa 来说调用链实际上已经完整：

```text
swe-bench/swe-bench-verified
          │
          ▼
     Harbor Dataset
          │
          ▼
      Harbor Trial
          │
          ▼
 HaifaCodingAgent
          │
          ▼
 Haifa CLI --message issue
          │
          ▼
       /testbed
      修改 repository
          │
          ▼
 Harbor SWE-bench Verifier
          │
          ▼
 official SWE-bench grading
          │
          ▼
    reward 0 / 1
```

你现在的 `HaifaCodingAgent` 已经完全符合这个 contract：`run()` 接受 `instruction + environment + context`，取得当前 workspace，再调用现成的非交互 JAR。

所以：

**Haifa Agent 主仓库 0 改动。**

---

## 2. Harbor SWE-bench 的实现方式也符合我们想要的架构

Harbor Adapter 从 HuggingFace：

```text
princeton-nlp/SWE-bench_Verified
```

读取：

```text
instance_id
repo
version
base_commit
problem_statement
patch
test_patch
...
```

然后转换成标准 Harbor Task：

```text
task/
├── instruction.md
├── task.toml
├── environment/
│   └── Dockerfile
├── tests/
│   ├── test.sh
│   └── config.json
└── solution/
    └── solve.sh
```

环境直接建立在 SWE-bench 官方预构建 task image 上：

```dockerfile
FROM {docker_image}

WORKDIR /testbed
```

因此 Haifa 进去之后看到的就是：

```text
/testbed
   └── repo @ base_commit
```

这与你现在：

```python
WORKSPACE=$(pwd -P)

java -jar haifa-agent.jar \
  --workspace "$WORKSPACE" \
  ...
```

天然匹配。

也就是说，**连 workspace adapter 都不用增加。**

---

# 3. 最关键：Verifier 真的是 SWE-bench semantics

Harbor 的 `tests/test.sh` 最终干的是：

```python
test_spec = make_test_spec(datum)

eval_status_map, found =
    get_logs_eval(test_spec, test_log_path)

report = get_eval_tests_report(
    eval_status_map,
    eval_ref,
    eval_type=...
)

get_resolution_status(report)
```

只有：

```text
ResolvedStatus.FULL
```

才：

```text
reward = 1
```

否则：

```text
reward = 0
```

所以我们上一轮讨论的：

```text
FAIL_TO_PASS
PASS_TO_PASS
repo-specific parser
FULL RESOLVED
```

全部还是 SWE-bench 官方逻辑。

这意味着 `haifa-agent-evals` 可以继续坚持现在这条原则：

> Harbor Verifier 是唯一 correctness authority。

完全不需要在 collector 里重新判 SWE-bench。

---

# 4. Harbor 甚至做了与官方 SWE-bench 的 parity 验证

这个非常重要。

Harbor 官方专门维护：

```text
adapters/swebench/parity_experiment.json
```

并做过 Full 500 题验证。Harbor Hub 当前列出的历史对比包括：

```text
codex + o4-mini
official     53.11
adapter      53.11

OpenHands + Claude Sonnet
official     66.8
adapter      67.0
```

以及更近期 mini-swe-agent + GPT-5-mini：

```text
Official SWE-bench:
56.3%

Harbor:
54.5% ± 0.7%
```

后一个比较是 499 个可比任务、Harbor 3 次运行。Harbor 也明确记录了那个异常任务以及一些 upstream harness 问题。([Harbor Hub][2])

所以我认为足够支持这样一个工程判断：

> **Haifa Agent 用 Harbor 跑 SWE-bench Verified，可以作为可信的 SWE-bench 评测结果；不需要再用官方 `run_evaluation.py` 重跑一套。**

---

# 5. 但你现有 `haifa-agent-evals` 有一个地方需要调整

不是 Runner。

是 **Admission 模型**。

你目前的 admission 要求每个 task：

```text
Oracle == PASS
NOP == FAIL
```

而且要求：

```text
local dataset manifest
+
local task digest
```

这是代码硬约束。

而 `dataset.py` 也明确支持两种模式：

```text
local task dataset
OR
registry dataset
```

没有 local manifest 时，runner 可以直接走 Registry；有 manifest 时才验证本地 task digest。

实际上你的 Runner 已经完全支持 SWE-bench：

```python
if tasks_path is None:
    dataset = {
        "name": dataset_name,
        "ref": dataset_ref,
        "task_names": list(config.tasks),
    }
```

然后直接交给 Harbor Registry。

也就是说：

```text
Haifa Runner
     ✅ 已支持

Haifa Agent Adapter
     ✅ 已支持

Collector
     ✅ 基本已支持

Finalizer
     ✅ 基本已支持

Admission
     ⚠️ 需要考虑 SWE-bench 策略
```

---

# 6. 为什么不能简单要求 SWE-bench 500 题全部 Oracle PASS

因为 Harbor 官方自己明确记录了少数 problematic tasks。

例如它列出了：

```text
astropy__astropy-8872
astropy__astropy-7606
astropy__astropy-8707
django__django-10097
```

以及：

```text
scikit-learn__scikit-learn-14710
sphinx-doc__sphinx-8595
sphinx-doc__sphinx-9711
```

其中有环境问题、超时问题以及 upstream SWE-bench harness 问题。

于是如果我们机械执行：

```text
500 × Oracle
500 × NOP
```

严格执行你现在 admission：

```text
Oracle < 1
    => REJECTED
```

那么整个 SWE-bench Verified dataset 很可能：

```text
REJECTED
```

但这不代表这个 benchmark 不能使用。

这正是我认为需要新增的一个概念。

---

# 7. 不建议降低 Admission 标准，而是区分两种 Dataset Trust

你现在的 Aider Polyglot 是：

```text
Haifa 自己挑题
+
自己修 verifier
+
自己生成 derived dataset
```

这里理应：

```text
PER_TASK_CALIBRATED
```

必须：

```text
Oracle PASS
NOP FAIL
digest frozen
```

但 SWE-bench Verified 是另一类：

```text
UPSTREAM_VERIFIED
```

它有：

```text
官方 benchmark
+
Harbor 官方 adapter
+
Harbor parity experiment
+
500 task registry dataset
```

我建议形成：

```text
DatasetTrustMode

PER_TASK_CALIBRATED
UPSTREAM_VERIFIED
```

不是让 SWE-bench 绕过可信性，而是改变可信证据来源：

```text
Aider Polyglot
──────────────
Haifa 自己证明：
Oracle PASS
NOP FAIL

SWE-bench Verified
──────────────────
外部证明：
benchmark identity
Harbor dataset digest
Harbor adapter identity
Harbor parity evidence
+
Haifa 本地 smoke calibration
```

这个领域模型反而更正确。

---

# 8. 第一轮不要直接跑 500 题

我建议现在建立：

```text
swebench-verified-smoke-v1
```

先 10～20 题。

目的不是测试 Haifa 成绩，而是测试整个 Eval contract。

比如从多个 repo 各取几题：

```text
django
sympy
matplotlib
scikit-learn
pytest
xarray
sphinx
astropy
```

不是挑容易题，而是覆盖不同代码库和测试系统。

第一轮验证：

```text
                    Haifa

SWE-bench task
      │
      ▼
Harbor environment
      │
      ▼
Java 21 / Haifa JAR installation
      │
      ▼
/testbed recognition
      │
      ▼
Agent modifies source
      │
      ▼
Haifa trace archived
      │
      ▼
SWE-bench verifier
      │
      ▼
reward
      │
      ▼
collector
      │
      ▼
finalize
```

先验证基础设施，而不是 score。

---

# 9. 配置最终甚至和你现在几乎一样

现在配置 parser 强制：

```text
dataset 必须有精确 @version
不能 latest/main/head
tasks 必须显式枚举
```

这一点非常好，不要改。

最终应当类似：

```yaml
id: coding-swebench-verified-smoke-v1

dataset: swe-bench/swe-bench-verified@<PINNED_REF>

tasks:
  - swe-bench/django__django-11099
  - swe-bench/sympy__sympy-13877
  - swe-bench/matplotlib__matplotlib-14623
  - swe-bench/scikit-learn__scikit-learn-14141
  - swe-bench/pytest-dev__pytest-7236

attempts: 1
timeoutMinutes: 30

candidates:
  - id: haifa
    agent: haifa_agent_evals.integrations.harbor.haifa_agent:HaifaCodingAgent
    model: deepseek-responses-flash
```

这里的 `<PINNED_REF>` 不应该手写猜测。

第一次：

```bash
harbor download swe-bench/swe-bench-verified
```

获取当前 Registry manifest，然后把实际 digest 固定进配置。

你现有所有：

```text
eval config digest
dataset digest
task digest
JAR digest
Harbor version
run manifest
```

机制继续工作。

---

# 10. 这里我发现一个值得提前处理的基础设施问题

Harbor SWE-bench adapter 当前 verifier 中有：

```python
# dependencies =
"swebench==4.0.3"
"datasets==2.16.1"
"fastcore<1.11"
```

然后：

```bash
uv run parser.py
```

并且生成环境 Dockerfile 本身还有：

```bash
curl ... install uv
```

这意味着它没有达到你现在 Aider Polyglot 环境那种：

```text
100% frozen
offline dependency
wheelhouse
Gradle cache
Cargo cache
```

的强度。

最初不应在没有运行证据时直接照搬：

```text
image prepare-tasks
```

那套复杂体系。

但是单题真实 Smoke 已经补齐了证据：第一次冷启动在 Task Dockerfile 的联网 `uv` 安装阶段超过
1800 秒并被 Harbor 判为 `EnvironmentStartTimeoutError`；同一环境构建完成进入本机缓存后，后续
Trial 才能正常执行。因此现在需要把“缓存偶然命中”提升为可核验的基线，而不是继续依赖随机 Trial
镜像名和 Docker build cache。

落地后的第一阶段是：

```text
验证 Registry Task 包 digest 与 upstream admission 完全一致
+
把一次成功构建的 /testbed 镜像重标记到稳定本地仓库名
+
将 RepoDigest 写入派生 task.toml，后续 Harbor 跳过 Dockerfile build
+
task-environment-lock.json 同时固定来源/派生 Task digest、image ID、OCI digest、大小和平台
+
固定 Harbor version
+
固定 dataset digest
```

入口为：

```text
evals image freeze-swebench
```

`doctor` 在付费运行前重新计算派生 Task digest、核对 admission 文件摘要，并通过本机容器后端检查
镜像身份。run manifest 同时记录 upstream `taskDigests`、`frozenTaskDigests` 和环境锁 SHA-256，
数据来源标记为 `local-frozen-environment`。这保持了“上游 Verifier/Task 是正确性事实源”和“本地
环境派生需要单独冻结”两套事实，不把派生 Task digest 冒充 Registry Task digest。

该基线只保证当前机器快速、确定地启动，不等同于可迁移 OCI 归档。若要跨机器运行，还需要导出/
导入镜像并保持 RepoDigest。后续扩到 5 题时逐题执行同一冻结流程；若发现 verifier 自身的：

```text
uv 下载
Docker image pull
JDK 下载
```

对可靠性影响明显，再继续做 verifier wheelhouse/offline cache：

```text
SWE-bench verifier dependency prewarm
```

Haifa 的 Java 21 则简单得多：你现有 Adapter 已经支持：

```text
HAIFA_EVAL_JAVA_ARCHIVE_PATH
```

把固定 JDK tar.gz 上传进去。正式 SWE-bench run 我建议**强制使用本地 pinned JDK archive**，不要让每个 trial 在线 curl JDK。

---

# 11. SWE-bench Multilingual 也已经有路了

这个也查到了。

Harbor 源码已经存在：

```text
adapters/swebench_multilingual/
```

它直接读取：

```python
SWE-bench/SWE-bench_Multilingual
```

并识别：

```text
Java
JavaScript
Go
Rust
...
```

同时保留：

```text
FAIL_TO_PASS
PASS_TO_PASS
test_patch
patch
```

Multilingual verifier 同样直接调用 SWE-bench 官方 grading，而且现在它甚至使用：

```text
swebench==4.1.0
```

不过我目前在 Harbor Hub 的公开 Dataset 列表中没有找到一个明显公开发布的 `swebench-multilingual` registry dataset；**Adapter 有，Registry Dataset 至少当前并不像 Verified 那样是一等公开入口。**

因此以后可以：

```text
Verified
    → Harbor Registry 直接用

Multilingual
    → Harbor 官方 adapter
    → 生成 local Harbor dataset
    → Haifa freeze manifest
```

这正好符合 `haifa-agent-evals` 已有能力。

---

# 12. 所以我现在给 Haifa SWE-bench 接入的改动规模判断

总体仍然很小，但不能只增加 YAML。当前实现有三个必须同时修正的门禁冲突：

1. 非 `--plan-only` 的 `evals run` 会强制执行 `doctor`，而 `doctor` 强制要求 admission；
2. `doctor` 当前只调用 `validate_local_dataset()`，Registry Dataset 会被误判为失败；
3. Registry 模式没有本地 manifest，当前 `run-manifest.json` 的 `taskDigests` 会为空；
4. CLI 会把 Doctor 的默认检查路径当成显式本地 Task 路径传给 Runner，从而阻断 Registry Dataset。

因此“先不改 Admission，通过现有 Runner 直接跑”的建议不可执行。正确的最小方案是显式增加
`UPSTREAM_VERIFIED` 信任模式，只信任仓库内受控目录中的 Dataset，并让 Registry admission 冻结
Dataset/Task digest；`doctor` 和 run manifest 消费同一份准入证据。

```text
haifa-agent
0 行核心代码改动

HaifaCodingAgent
原则上 0 行

runner.py
补充 Registry Task digest 到 run manifest

collector.py
第一轮 0 行

finalizer.py
第一轮 0 行

config.py
增加显式 dataset trust mode
```

第一轮增加：

```text
evals/
  coding-swebench-verified-smoke-v1.yaml
```

以及 Registry admission、Doctor 分支和对应的单元测试。不新增 Agent、Harness、Verifier 或数据库。

需要改动的代码范围是：

```text
admission.py
doctor.py
runner.py（仅补运行指纹）
config.py（仅补显式信任模式）
environment_baseline.py（冻结并校验本地 Task 环境）
cli.py（暴露 freeze-swebench 入口）
```

先生成精确 Registry admission，再通过现有 Runner 跑 **1 题 → 5 题**，确认 Haifa 在这个环境里完整
工作。不能绕过已经建立的付费运行前门禁。

---

## 我建议现在实际推进顺序

```text
Phase 0
Registry admission + 单题 infrastructure probe + Task 环境基线冻结
        ↓
Phase 1
5题 SWE-bench smoke
        ↓
确认：
Haifa install
workspace=/testbed
model connectivity
source modifications
trace archive
official verifier
collector/finalizer
        ↓
Phase 2
定义 UPSTREAM_VERIFIED admission
        ↓
Phase 3
20~30题固定 regression set
        ↓
Phase 4
SWE-bench Verified 500
        ↓
Phase 5
SWE-bench Multilingual
```

首轮 Phase 1 已在 `coding-swebench-verified-representative-5-v1` 上完成。固定集合为 Requests、Flask、
Pytest、Django、SymPy，难度分层是 2 道 `<15 min fix`、2 道 `15 min - 1 hour`、1 道
`1-4 hours`。使用 `deepseek-responses-flash` 单并发运行 22 分 31 秒，结果为 3/5 PASS：

```text
PASS  pallets__flask-5014
PASS  psf__requests-1142
PASS  pytest-dev__pytest-8399
FAIL  django__django-16950  (NonZeroAgentExitCodeError, Verifier 仍执行并返回 0)
FAIL  sympy__sympy-17630    (Agent 干净完成，Verifier 返回 0)
```

5/5 Trial 有效，SQLite/Trace/Transcript 证据完整，无 `TOOL_OUTCOME_UNKNOWN`，最终归档
`COMPLETE`。五个环境初始化耗时为 4.755～5.304 秒，证明 digest-pinned 环境解决了冷 Dockerfile
构建超时；3/5 只能说明这批 Pipeline Smoke 的结果，不能外推为 SWE-bench Verified 分数。

同一批五题随后通过 `coding-swebench-verified-representative-5-qwen37-v1` 使用百炼
`qwen3.7-max` 复跑，直接复用五个冻结 image ID，没有下载新题镜像。运行总耗时 38 分 52 秒，结果为
0/5 PASS：Django、Pytest 的 Agent 干净完成，Flask、Requests、SymPy 为
`NonZeroAgentExitCodeError`。5/5 Trial 仍全部有效，SQLite/Trace/Transcript 完整，无
`TOOL_OUTCOME_UNKNOWN`，finalizer 为 `COMPLETE`（0 invalid、0 evidence issue）。

因此 SWE-bench Verified 的 Registry admission、冻结 Task 环境、DeepSeek/百炼 Provider、官方
Verifier 和证据归档链路都已经真实跑通。模型正确性仍必须单独看 Harbor Reward：DeepSeek 是 3/5，
本次 Qwen 是 0/5；不能用“流水线有效”替代“题目通过”。下一阶段应先分析 Qwen 三个非零退出和两个
干净完成但 Verifier FAIL 的轨迹，再决定是否扩大固定回归集，而不是直接外推到 Verified 500。

[1]: https://hub.harborframework.com/datasets/swe-bench/swe-bench-verified/latest?utm_source=chatgpt.com "Harbor Hub"
[2]: https://hub.harborframework.com/datasets/swe-bench/swe-bench-verified/latest "Harbor Hub"
