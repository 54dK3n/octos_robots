# 轮足双臂机器人 · 任务编排层

一句话进来，拆成一串技能调用，逐步执行，失败重试。

## 跑起来

```bash
cd robot-octos-demo
chmod +x skills/*/main          # 必须先执行这一步
python3 run.py "把地上的球捡起来放桌上"
```

无依赖，Python 3.8+ 即可，不联网。

其他几条：

```bash
python3 run.py "把球捡起来" --retries 0    # 看失败中止
python3 run.py "帮我叠衣服"                # 看拆不出来时怎么办
python3 run.py --list-skills               # 看已注册技能
```

## 目录

```
run.py                    入口：拆解 → 校验 → 执行
orchestrator/
  planner.py              一句话 → 步骤列表（mock / llm 两种模式）
  executor.py             DISPATCH→EXECUTE→JUDGE→RETRY 状态机  ← 核心
  judge.py                判定层，现在是 mock，以后接视觉
skills/
  registry.json           技能注册表  ← 核心交付物
  move_to/main            假技能，以后换成蒋丰泽的底盘
  pick/main               假技能，以后换成曹志伟的 VLA
  place/main              假技能
contracts/
  skills.md               技能接口契约草案，给队友看的
```

## 建议阅读顺序

1. `skills/registry.json` — 技能长什么样
2. `run.py` — 主流程三步
3. `orchestrator/executor.py` — 状态机怎么转的
4. `contracts/skills.md` — 要跟队友对齐什么

## 接真东西的顺序

| 阶段 | 换掉什么 | 其余部分 |
|---|---|---|
| 现在 | 全 mock | — |
| 下一步 | `--planner llm` 接真大模型 | executor 不动 |
| 之后 | `skills/move_to/main` 换成 SimCar 调用 | 其余不动 |
| 再之后 | `skills/pick/main` 换成曹志伟 `POST /v1/execute` | 其余不动 |
| 最后 | `judge.py` 换成真视觉判定 | 其余不动 |

每次只换一格，其余不动 —— 这就是留 provider 层的意义。

## 接真大模型

```bash
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_API_KEY=sk-xxx
export LLM_MODEL=gpt-4o-mini
python3 run.py "把地上的球捡起来放桌上" --planner llm
```

`planner.py` 里的 `plan_mock` 是规则拆解，只用来先把循环跑通，不是交付物。
真正要打磨的是 `PLANNER_SYSTEM_PROMPT` 和 `validate_plan`。

## 和 Octos 的关系

Octos 是可选的工具调用层，**不是这套东西的地基**。
上面的 planner / executor / judge / registry 跟框架无关，
将来换 agent_vla 也不用重写。

要接 Octos，把 `skills/*/main` 注册成 Octos 本地 skill 即可，
调用协议（stdin 收 JSON、argv[1] 收技能名）是刻意对齐的。

## 下一步

- [ ] `--planner llm` 跑通，记录 20 条指令的拆解成功率
- [ ] 拆错的案例单独存一份，这是后面唯一能证明系统好用的东西
- [ ] `contracts/skills.md` 发给曹志伟和蒋丰泽，把待确认项敲掉
- [ ] 找带教确认：最终 demo 用 Octos 还是 agent_vla
