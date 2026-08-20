# Evaluation Protocol v1（MVP）

> 状态：Ready for implementation
> 日期：2026-08-19

## 1. 公平规则

- 两个 Candidate 使用完全相同的 6 个 Task IDs；
- 每个 Attempt 使用全新 Harbor Container 和 Workspace；
- 时限统一为 20 分钟；
- 可见 Task、测试和网络条件一致；
- Candidate、模型、JAR、Dataset、Verifier 和 Harbor 使用精确版本；
- 不在 Attempt 中人工审批、补充 Prompt 或修复环境；
- 运行后不更换低分 Task。

若模型无法保持一致，允许做完整系统对比，但报告必须写明模型差异，不做单因素归因。

## 2. 结果状态

只使用三种状态：

| 状态 | 规则 |
| --- | --- |
| PASS | Candidate 获得有效机会，Verifier 有效且通过 |
| FAIL | Candidate 获得有效机会，Verifier 有效但未通过；包括超时、无修改和 Candidate 内部失败 |
| ERROR | Environment、Adapter、Credential 注入或 Verifier 无法产生可信评分 |

CLI 退出 0 不等于 PASS；CLI 退出 1/2 也不自动等于 ERROR。只要 Task 和 Verifier 仍有效，Candidate 自身
失败就是 FAIL。

## 3. 重试

- PASS/FAIL 不自动重试；
- ERROR 只允许在确认是暂态基础设施故障后重试 1 次；
- 原 Trial 必须保留；
- 报告显示重试次数和最终状态；
- 不选择最好一次替换失败结果。

## 4. 评分

- Harbor Task Verifier 是唯一正确性来源；C++ 使用受版本控制的派生版本，只修复编译失败后未写 reward
  的控制流，不修改测试断言或通过标准；
- stdout、自然语言回答、CLI exit 和 Trace 不用于评分；
- Verifier 自身失败记 ERROR，不给 Candidate 记 0 分；
- 有效通过率为 `PASS / (PASS + FAIL)`；
- 报告同时显示 Planned、Valid、Errors，避免缩小分母。

MVP 不计算置信区间、Bootstrap、pass@k 或综合能力分。

## 5. 资源

- Candidate timeout：20 分钟；
- Harbor Agent timeout：20 分钟；
- Verifier timeout：10 分钟；
- 本地并发：1，链路稳定后最多 2；
- Haifa 使用当前配置的 50 Iterations、32 Tool Calls；
- 真实调用总数默认不超过 12；
- 实际费用在调用前设置总上限，无法可靠取得 Token/Cost 时显示 unavailable。

## 6. 安全

- Credential 只通过环境注入，不进入 YAML、argv、日志和报告；
- Container 不挂载用户 Home、源码仓、Docker Socket 或 Git/GitHub 凭据；
- Hidden Tests、Gold Patch 和其他 Candidate 结果不可见；
- Haifa 只启用文件与命令工具；
- 原始 Harbor Jobs 写入 `work/` 并忽略；
- stdout/stderr、Trace 和报告发布前扫描秘密；
- 超时后必须收敛 Java、Shell 和 Tool 子进程。

## 7. 报告限制

`coding-smoke-v1` 只有 6 Tasks × 1 Attempt。报告必须声明：

- 结果只证明 MVP 链路和本次小样本表现；
- 不能代表全部语言、真实大型仓库或长任务；
- 不能做统计显著性结论；
- 模型不同时只能比较完整 Candidate System；
- ERROR 必须逐项披露。
