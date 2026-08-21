# 评测基础设施改进优先级

> 状态：Proposed
> 日期：2026-08-21
> 输入：`coding-polyglot-30-v1` 首轮真实评测、Python/Rust 专项复盘与完整轨迹归档
> 既有实施计划（执行时按本文优先级重排）：[`07-next-stage-evaluation-reliability-plan.md`](07-next-stage-evaluation-reliability-plan.md)

## 1. 这轮改进要解决什么

首轮 30 题已经证明最小评测链路可以工作：Harbor 负责隔离执行与评分，`haifa-agent-evals` 负责配置、收集和报告，SQLite/JSONL 保存 Agent 轨迹。它同时暴露出一个比“再多跑一些题”更优先的问题：**一次 FAIL 可能来自 Agent 能力、模型调用、运行协议、依赖环境、题目契约或 Verifier，现有总分无法区分这些层次。**

下一步目标不是建设通用评测平台，而是让每一次付费评测满足三点：

1. 题目和 Verifier 在运行前已经被证明可用于比较；
2. 正式成绩保持官方口径，同时有足够证据解释失败发生在哪一层；
3. 同一活动可以复现、审计和安全归档，不会覆盖或挑选有利结果。

固定主链路仍然是：

```text
eval.yaml
  -> admit / doctor
  -> Harbor Candidate × Task
  -> collect / validate
  -> results.csv
  -> comparison.md
  -> finalize
```

不增加第二套 Harness、数据库、调度器或评分引擎。

## 2. 按边际效益排序

边际效益按“减少一次无效付费运行或错误结论的程度”排序，而不是按实现容易程度排序。

| 排名 | 改进项 | 主要收益 | 难点 | 建议阶段 |
| --- | --- | --- | --- | --- |
| 1 | Dataset/Verifier 准入门禁 | 在付费前拦截错误题面、偏置 Verifier、缺失依赖和不可达奖励 | 需要为不同语言验证统一的不变量，又不能重造 Harbor | P0 |
| 2 | 成绩与运行有效性分层 | 避免把基础设施故障、协议失败或 Verifier 缺陷误判为 Agent 能力 | 必须保留官方 FAIL，不能用诊断口径篡改总分 | P0 |
| 3 | 运行前检查、指纹与完整收尾 | 显著减少镜像、JAR、凭据、缓存和矩阵缺失造成的整批浪费 | 跨宿主、容器和 Harbor 证据关联 | P0 |
| 4 | Attempt 保全与受控重跑 | 可以诊断偶发故障，又不产生 best-of 偏差 | 要区分正式计分、基础设施恢复和诊断重跑 | P1 |
| 5 | 轨迹完整性与失败层摘要 | 让 30 题复盘从人工翻日志变成可复算事实 | SQLite、JSONL、Harbor Job 和报告需稳定关联 | P1 |
| 6 | 候选公平性与样本设计 | 让 Candidate 差异更可能来自能力，而不是运行条件 | 模型、预算、缓存、镜像和超时必须成对一致 | P1 |
| 7 | Web、长期趋势、分布式运行 | 对规模化有价值，但当前不会提高单次结论可信度 | 容易过早平台化 | 暂缓 |

## 3. P0-1：Dataset/Verifier 准入门禁

### 3.1 第一性原则

评测题不是“有题面和测试就能跑”。一个可比较的 Task 至少需要满足：

- Candidate 能从题面推导出公开契约；
- Oracle 在固定环境中必然得到 PASS；
- NOP 或已知错误实现必然得到 FAIL；
- Verifier 的所有退出路径都能产生明确 Reward；
- 选中的测试集合与题目声明一致，不能只跑到一个偶然通过的测试；
- 依赖、编译器、网络和缓存要求在冻结镜像中可满足。

Rust 首轮复盘中出现的题面/测试契约偏差、只选择部分测试，以及多题在首次写入前失败，都说明这一门禁的收益最高。

### 3.2 最小实现

在现有 Python CLI 中增加小型只读动作，不增加服务：

```text
evals admit --config evals/coding-polyglot-30-v1.yaml
```

每个 Task 输出一条准入记录：

- Dataset ID、版本、digest、Task ID；
- 题面和 Verifier 文件 digest；
- Oracle 结果、NOP 结果；
- Verifier 实际选中/发现/忽略的测试数（能够可靠获得时）；
- 固定镜像、依赖准备和网络要求；
- `ADMITTED / REJECTED / MANUAL_REVIEW` 与脱敏原因。

通用门禁只验证不变量。语言专属命令继续由 Dataset/Harbor Task 定义，`haifa-agent-evals` 不内置 Python、Rust、Java 等语言工作流。

### 3.3 数据集修正规则

- 已产生正式结果的 Dataset 内容不得原地修改；
- 修正题面或 Verifier 必须生成新 digest，必要时提升 Dataset 版本；
- 新旧结果不能汇总为同一活动得分；
- 报告可以并列展示“原始官方结果”和“修正版诊断结果”，但必须明确口径；
- 准入证据随运行归档，不能只保留一条“已检查”布尔值。

## 4. P0-2：官方成绩与运行有效性分层

### 4.1 不改写 Harbor 成绩

正式计分仍以 Harbor Reward 为唯一依据：PASS 就是 PASS，FAIL 就是 FAIL。即使 Verifier PASS 而 Agent 最终因完成协议异常退出，也不能在原始成绩上偷偷改成 PASS。

同时新增正交诊断维度：

```text
official_result        PASS | FAIL | ERROR
trial_validity         VALID | INVALID_INFRA | INVALID_DATASET | INCOMPLETE
agent_clean_exit       true | false
workspace_changed      true | false | unknown
verifier_executed      true | false
verifier_selected      integer | unknown
verifier_discovered    integer | unknown
failure_stage          preflight | model | tool | completion | verifier | finalize
failure_code           stable code | unknown
model_attempts         integer | unknown
tool_outcome_unknown   true | false
```

这些字段用于回答“失败发生在哪一层”，不能反向修改 `official_result`。

### 4.2 报告至少给出三组数

1. **Official score**：严格按冻结 Dataset 和 Harbor Reward；
2. **Valid trial rate**：多少 Trial 具备完整、可信、可归因的运行证据；
3. **Clean completion rate**：Agent 是否按协议正常完成，与 Verifier 是否通过分开统计。

如果 5 道题 Verifier PASS 但 Agent 异常退出，报告应同时保留这两个事实，不能只选对某一方有利的数字。

### 4.3 暂不做自动根因 AI

第一版只做确定性抽取和有限分类。复杂归因留给人工复盘；不让另一个模型读取全部轨迹后直接生成“真相标签”，避免不可复算和额外费用。

## 5. P0-3：Preflight、运行指纹与 Finalize

### 5.1 运行前检查

```text
evals doctor --config <eval.yaml>
```

在模型调用前检查：

- 配置可解析、Dataset 已冻结且已准入；
- Harbor、Container backend、基础镜像和 Task 可解析；
- Candidate 命令、Haifa JAR/config 与运行时存在，digest 可计算；
- Credential 仅检查存在性，不读取或打印值；
- 运行目录可写、磁盘空间足够；
- 离线运行所需依赖已经进入基础镜像或 Task 缓存；
- 预期 Candidate × Task 矩阵唯一且完整。

需要下载镜像或依赖时使用显式 `prepare`，不要让 `doctor` 隐式修改环境。

### 5.2 不可变运行指纹

每次活动生成唯一 `run-id` 和 `run-manifest.json`，至少记录：

- Eval 配置、Dataset、Task 和 Verifier digest；
- Candidate、模型路由、预算和超时；
- Haifa JAR/config digest；
- Harbor、容器后端、基础镜像 digest；
- started/finished 时间与预期矩阵。

Manifest 只记录运行溯源，不定义第二套 Trial/Result Schema；不得包含凭据、完整 Prompt、reasoning、供应商原始响应或可发布范围外的宿主绝对路径。

### 5.3 一键收尾

`finalize` 只编排已有能力：

1. 校验 Trial 数量、唯一性和状态；
2. 收集 Harbor 原始结果与诊断字段；
3. 校验 SQLite/JSONL 轨迹关联和基本完整性；
4. 生成 CSV 与 Markdown；
5. 执行 secret、reasoning、provider response 和宿主路径扫描；
6. 记录文件 digest；
7. 检查容器和子进程是否收敛。

Candidate FAIL 不应让收尾程序崩溃；证据缺失或矩阵不完整必须让 `finalize` 非零退出。

## 6. P1：Attempt、轨迹与公平性

### 6.1 保全每次 Attempt

明确区分：

- `score_attempt`：预先声明、计入正式成绩；
- `infra_recovery_attempt`：仅在确认 Candidate 尚未获得有效执行机会时使用；
- `diagnostic_rerun`：不计分，用于复现和定位。

任何重跑都生成新 Trial ID，保留原失败证据；禁止覆盖、删除失败 Attempt 或事后选择最好结果。

### 6.2 轨迹完整性

SQLite 仍是 Haifa 运行事实源，JSONL 是便于交换和查看的投影。收集器只增加关联与校验，不复制 Runtime 数据模型。至少校验：

- Eval run、Harbor Job、Trial、Agent Run ID 可双向关联；
- JSONL 首尾、事件数量和 checksum 与归档清单一致；
- ToolCall、ToolResult、ChangeSet 和最终状态不存在明显悬空；
- stdout/stderr 被截断时有显式标记，而不是静默缺失。

### 6.3 成对公平

同一正式比较中的 Candidate 必须使用相同：

- Dataset/Task/Verifier digest；
- 基础镜像、网络策略和依赖缓存；
- 时间、模型调用和输出预算；
- Attempt 数和超时口径。

模型不同是被测变量时应显式记录；模型相同时应固定精确模型标识和路由，不做隐式 fallback。

首轮稳定前仍以成对 30 题为主。重复运行、置信区间和 pass@k 只有在准入、完整性和重跑规则稳定后才有意义。

## 7. 保持简单的组件边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Harbor | 容器隔离、Task 执行、Verifier、Reward、原始 Job | Haifa 轨迹语义、跨 Candidate 报告 |
| Dataset | 题面、Fixture、Verifier、冻结身份 | Candidate 编排、长期报告 |
| `haifa-agent-evals` | 配置、准入编排、preflight、收集、校验、报告、归档 | 第二套沙箱、第二套评分、通用工作流引擎 |
| Candidate | 执行题目并产出自己的轨迹 | 修改评测口径 |
| 人工 Review | 审核歧义契约、复杂归因和正式结论 | 手工修饰原始成绩 |

建议只在现有 CLI 增加 `admit`、`doctor`、`finalize`，并扩展 `collect/report`；公共逻辑仍放 Python，PowerShell/Shell 入口保持薄包装。

## 8. 实施切片

### Phase 1：准入与口径

- 建立 Task 准入记录和冻结规则；
- 为当前 30 题跑 Oracle/NOP 和 Verifier 选择检查；
- 分离 official、validity、clean completion 字段；
- 将有缺陷的题集固定为新版本，不改写历史。

### Phase 2：Preflight 与完整性

- 实现 `doctor`；
- 完善运行指纹和预期矩阵；
- 缺失、重复、未知 Trial 阻止正式报告。

### Phase 3：轨迹与 Finalize

- 关联 Harbor Job、Run ID、SQLite 和 JSONL；
- 实现确定性失败层摘要；
- 一键生成可复算、经脱敏扫描的归档。

每个 Phase 先用离线 Fixture 验证，再用 1～2 道题做真实环境冒烟；前一 Phase 稳定后才运行下一批 30 题。

## 9. 验收标准

1. 错误题面、Oracle 不通过、NOP 意外通过或 Verifier 无 Reward 会在模型调用前失败；
2. 修正 Dataset 有新 digest，旧结果保持原样；
3. 报告可同时展示官方结果、有效 Trial 和干净完成率，三者可从 CSV/原始证据复算；
4. 缺失、重复或未知 Trial 不能生成看似完整的正式得分；
5. 同一配置连续运行不会覆盖或串入历史结果；
6. 每个 Trial 可定位到 Harbor Job 和已归档轨迹；
7. 报告和归档不含秘密、reasoning、原始供应商响应或不必要的宿主路径；
8. 系统仍只有 Harbor 一个执行和评分引擎。

## 10. 暂不实施

- Web UI、Dashboard、公开排行榜；
- 新数据库、长期 Baseline Service、Dataset Registry；
- 分布式 Worker、队列和调度控制面；
- 自动 LLM 根因分析；
- 为每种语言编写一套 Evals 工作流；
- 在证据质量稳定前引入复杂统计或扩大题量。

这些能力可能有价值，但当前边际效益显著低于“先保证题目、运行和结论可信”。

## 11. 重点人工 Review/Test

- 题面公开契约是否足以通过 Verifier，特别是运算符、单位、错误语义和精确文本；
- Verifier 是否运行了预期测试全集，而非偶然选中单个测试；
- Dataset 修正是否真正产生新身份，报告是否避免混分；
- `INVALID_INFRA` 的判定是否足够严格，避免给 Agent 失败找借口；
- `infra_recovery_attempt` 是否可能被滥用为免费重试；
- Candidate 的模型、预算、网络、镜像和超时是否成对一致；
- Secret scan 是否覆盖 Trace、stdout/stderr、Manifest、CSV 和 Markdown；
- timeout 后 Container 与 Java/Shell/Tool 子进程是否真正退出。

## 12. 方法依据

本方案采用“任务专属、自动化、记录全部证据，并用人工校准自动评分”的评测原则；分数不是唯一信号，工作流级 Agent 应结合轨迹定位失败步骤。参见：

- [OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
- [OpenAI — Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)
- [Harbor documentation](https://harborframework.com/docs)
