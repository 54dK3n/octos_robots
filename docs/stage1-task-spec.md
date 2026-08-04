# 阶段 1 任务书：拆解 → 校验 → 执行 的最小闭环

> 状态：已完成（本文档为补录，配合 `benchmarks/` 一起补齐前置材料）
> 交付物：`orchestrator/`、`skills/`、`run.py`、`contracts/skills.md`

---

## 1. 目标

把"一句中文指令"变成"一串可执行、可校验、可重试的技能调用"，
先用全 mock 的方式把整条链路跑通，再逐段替换成真实实现。

链路：

```
指令 → planner 拆解 → validate_plan 校验 → executor 逐步执行（DISPATCH→EXECUTE→JUDGE→RETRY）
```

## 2. 已完成的模块

- `skills/registry.json` —— 技能注册表，同时喂给 planner（当工具定义）和 executor（当派发表）
- `orchestrator/planner.py` —— 两种拆解模式：
  - `plan_mock`：规则拆解（正则），不联网，只用来先把循环跑通，不是交付物
  - `plan_llm`：真调 OpenAI 兼容接口，只用标准库；JSON 格式解析失败时有一次"格式修复"（不涉及任务拆解本身）
  - `validate_plan`：逐条校验技能是否已注册、必填参数是否齐全。**只拒绝，不修复**——这是阶段 1 的已知局限，也是下一轮任务（计划级修复）要解决的问题
- `orchestrator/executor.py` —— DISPATCH → EXECUTE → JUDGE → RETRY 状态机，跟 Octos / agent_vla 无关，换 skill 实现不用碰这层
- `orchestrator/judge.py` —— mock 判定层，直接采信技能自报的 `success`，以后换真视觉判定
- `run.py` —— 入口，串联拆解 / 校验 / 执行三步

## 3. 验收（阶段 1）

- [x] `python3 run.py "把地上的球捡起来放桌上"` 4/4 步骤成功
- [x] `--retries 0` 能看到失败中止
- [x] `--planner llm` 接得上真实模型（需要 `LLM_BASE_URL` / `LLM_API_KEY`）
- [x] `--dry-run` 只拆解校验不执行
- [x] `contracts/skills.md` 定稿草案，发给曹志伟 / 蒋丰泽对齐接口

## 4. 遗留问题（留给下一轮）

阶段 1 的 `validate_plan` 是一道纯拒绝闸门：模型漏参数、编技能名，
直接判不合法、整条指令失败，**没有任何工程手段挽回模型的错误**。

这导致基准报告里 `raw`（模型第一次输出直接能不能用）和
`final`（走完整流程能不能用）两个指标几乎相等——没有任何一层在
"模型出错" 和 "任务失败" 之间做事，这个对照因此没有意义。

下一轮任务（计划级修复，见交接记录）要解决的正是这个问题：
校验不过时，把 `validate_plan` 返回的具体问题回传给模型，
给一次、且只给一次重新规划的机会，让 `final` 真正高于 `raw`。

## 5. 基准方法论（`benchmarks/`）

- `benchmarks/cases.json`：20 条中文指令，每条标注 `expect`
  （`solvable`/`unsolvable`）和 `solvable` 时期望的技能序列
  `expected_skills`（只比技能名顺序，不比参数字面量——
  `validate_plan` 本来就不做语义校验，这是系统当前的真实边界）
- `benchmarks/run_bench.py`：跑 20 条指令，统计 `raw`（模型第一次
  输出直接判定）和 `final`（走完整流程后判定，`llm` 模式下含至多
  一次计划级修复）两个成功率；另外用 5 份手写的错误计划直接考
  `validate_plan`（"校验层"），跟 planner 用的什么模式无关
- `mock` 模式下 `raw == final` 恒成立，这是预期行为——mock 没有
  "模型输出、校验失败、回传错误重新问" 这个环节，`raw/final` 的
  差距只有在 `--planner llm` 时才有意义
