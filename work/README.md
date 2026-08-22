# `work/` 目录结构

`work/` 是本仓库的本地评测工作区。除本说明文件外，目录内容均由 Git 忽略；正式收尾后的可复核归档仍写入 `run_data/`，不要把 `work/` 当作长期事实源。

## 目录分层

```text
work/
├── runs/
│   ├── evaluations/      # 正式评测、重试和 smoke Harbor Jobs
│   ├── calibration/      # oracle / nop 校准 Jobs
│   └── preflight/        # doctor、Compose 网络预检和 trace 预检
├── tasks/
│   ├── source/           # 原始数据集副本
│   ├── selected/         # 筛选后的 Tasks
│   ├── derived/          # 修订或派生 Tasks
│   ├── prepared/         # 按评测版本准备的 Task 集合
│   ├── candidates/       # 候选 Task 集合
│   └── subsets/          # 临时评测子集
├── cache/
│   ├── images/           # Agent 镜像上下文、Task 环境和语言依赖缓存
│   ├── downloads/        # JDK/JRE/Gradle/OCI/Wheel 离线下载与分片
│   └── tooling/          # Harbor/容器兼容工具副本
├── gates/
│   ├── admissions/       # 数据集准入结果
│   ├── infrastructure/   # 基础设施证据
│   └── preflight/        # 旧版独立预检证据
├── operations/
│   ├── configs/          # 可复用评测、校准、预检和代理配置
│   ├── compose/          # Compose overlays
│   ├── controllers/      # 多批次运行控制器
│   ├── scripts/          # 下载、代理和诊断辅助脚本
│   ├── generated/        # 历史生成的 Harbor plan/job/manifest
│   └── runtime/          # PID 和本地运行日志
└── diagnostics/
    ├── probes/           # 本地 Agent 探针
    ├── patch-checks/     # Task/verifier 补丁验证
    ├── verifier-smoke/   # verifier 冒烟 Jobs
    └── reports/          # 临时对比报告
```

## 默认写入位置

- `evals run`：`work/runs/evaluations/<eval-id>/<run-id>/`
- `evals doctor`：`work/runs/preflight/doctor/<eval-id>.json`
- Compose 网络预检 Jobs：`work/runs/preflight/harbor/<run-id>/`
- 默认准入文件：`work/gates/admissions/<eval-id>.json`
- 默认派生 Tasks：`work/tasks/derived/`
- 镜像与 Task 环境缓存：`work/cache/images/`

每个 Harbor Job 的控制文件仍和 Job 目录放在同一父目录，便于 `collect`、`report` 和 `finalize` 按 `run-id` 定位。

## 管理规则

1. 不按“看起来过期”直接删除文件；先确认没有活动进程，再完成 `finalize` 或复制到 `run_data/`。
2. 移动目录必须使用不覆盖目标的同盘移动，并核对文件数、总字节数；重要迁移应做逐文件哈希校验。
3. `result.json`、`config.json`、`lock.json`、Job 日志和已生成 manifest 中记录的旧绝对路径是历史证据，不因目录迁移而改写。
4. 可复用配置、控制器、脚本和缓存 locator 必须使用本文件中的新路径。
5. API Key 只通过环境变量注入；不得把密钥、完整模型响应或 reasoning 写入此目录。

## 2026-08-22 迁移说明

本次迁移分批移动并校验了 7036 个文件（2,353,449,995 字节），迁移前后逐文件 SHA-256 一致，没有删除或覆盖文件。reconciliation bundle 在其 5 个 trial 全部结束且进程链退出后才执行移动；`proxy_relay.py` 在不停止既有代理进程的前提下移动到 `operations/scripts/`，移动后进程保持运行。
