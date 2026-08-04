"""Planner —— 一句话 → 步骤列表。

这是你整条链路里独有的那一环：别人都是"一句话 = 一个动作"，
只有你要把一句话拆成好几步。

两种模式：
  mock  规则拆解，不联网，先让循环跑起来
  llm   真调大模型，一次性出全部步骤（不做 ReAct）

一次性出全部步骤的理由（面谈时要能讲清楚）：
  1. 边缘小模型每轮推理都要几秒，ReAct 逐轮思考延迟顶不住
  2. 一次性输出可复现、可回放、可做基准测试
  3. 计划出来后是一个固定列表，安全闸门有明确的插入点
"""
import json
import os
import re
import urllib.request

PLANNER_SYSTEM_PROMPT = """你是机器人任务规划器。

把用户的中文指令拆成一串技能调用，一次性输出全部步骤，不要分轮思考。

可用技能：
{skills}

只输出 JSON 数组，不要任何解释、不要 markdown 代码块。格式：
[{{"skill": "技能名", "args": {{"参数名": "参数值"}}}}]

规则：
- 只能使用上面列出的技能，不许编造
- 抓取前必须先移动到目标附近
- 放置前手上必须已经有东西
- 指令做不到时输出空数组 []

示例 1（四步任务）：
指令：把地上的球捡起来放桌上
输出：[{{"skill": "move_to", "args": {{"target": "球"}}}}, {{"skill": "pick", "args": {{"object": "球"}}}}, {{"skill": "move_to", "args": {{"target": "桌子"}}}}, {{"skill": "place", "args": {{"target": "桌子"}}}}]

示例 2（单步任务）：
指令：去垃圾桶那边
输出：[{{"skill": "move_to", "args": {{"target": "垃圾桶"}}}}]

示例 3（做不到）：
指令：帮我叠衣服
输出：[]
"""


def _format_skills(registry: dict) -> str:
    """把注册表渲染成给大模型看的工具说明。"""
    lines = []
    for skill in registry["skills"]:
        params = ", ".join(
            f"{name}({meta['type']})"
            for name, meta in skill["params"].items()
        )
        lines.append(f"- {skill['name']}({params}): {skill['description']}")
    return "\n".join(lines)


def plan_mock(text: str, registry: dict) -> tuple:
    """规则版拆解。够用来验证循环，不用来交付。

    没有模型可言，raw 就用计划本身的 JSON 文本占位，
    这样调用方不用区分 mock/llm 两种返回形态。
    """
    text = text.strip()
    plan = []

    # 抓 X 放到 Y
    match = re.search(r"把(.{1,6}?)(?:捡起来|拿起来|抓起来|捡|拿).*?放(?:到|在|进)?(.{1,6}?)(?:上面|里面|上|里|中)?$", text)
    if match:
        obj, target = match.group(1), match.group(2)
        obj = re.sub(r"^(地上的|桌上的|那个|这个)", "", obj)
        plan = [
            {"skill": "move_to", "args": {"target": obj}},
            {"skill": "pick", "args": {"object": obj}},
            {"skill": "move_to", "args": {"target": target}},
            {"skill": "place", "args": {"target": target}},
        ]
    else:
        # 只抓不放
        match = re.search(r"把?(.{1,6}?)(?:捡起来|拿起来|抓起来)", text)
        if match:
            obj = re.sub(r"^(地上的|桌上的|那个|这个)", "", match.group(1))
            plan = [
                {"skill": "move_to", "args": {"target": obj}},
                {"skill": "pick", "args": {"object": obj}},
            ]
        else:
            # 只移动
            match = re.search(r"(?:去|走到|移动到)(.{1,6}?)(?:那里|旁边|附近)?$", text)
            if match:
                plan = [{"skill": "move_to", "args": {"target": match.group(1)}}]

    raw = json.dumps(plan, ensure_ascii=False)
    return plan, raw


def _call_llm(messages: list) -> str:
    """发一次请求，返回模型的原始文本。

    需要三个环境变量：
        LLM_BASE_URL   例如 https://api.openai.com/v1
        LLM_API_KEY
        LLM_MODEL      例如 gpt-4o-mini

    temperature 固定为 0：任务分解要求可复现，不许暴露成参数。
    """
    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if not base_url or not api_key:
        raise RuntimeError("请先设置 LLM_BASE_URL 和 LLM_API_KEY")

    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": messages,
    }).encode()

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)

    return payload["choices"][0]["message"]["content"].strip()


def _strip_fence(text: str) -> str:
    """去掉模型可能包裹的 markdown 代码块围栏。"""
    return re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()


def plan_llm(text: str, registry: dict) -> tuple:
    """真调大模型。走 OpenAI 兼容接口，只用标准库，不装依赖。

    JSON 解析失败时给一次修复机会：把模型的错误回答原样喂回去，
    再追加一句要求只输出 JSON 数组，重新问一次。
    注意——这一轮补发只修 JSON 格式，不让模型重新思考任务该怎么拆。
    任务分解仍然是一次性的，与 ReAct 的逐轮重新推理有本质区别。
    """
    messages = [
        {
            "role": "system",
            "content": PLANNER_SYSTEM_PROMPT.format(
                skills=_format_skills(registry)
            ),
        },
        {"role": "user", "content": text},
    ]

    raw_first = _call_llm(messages)
    cleaned = _strip_fence(raw_first)
    try:
        return json.loads(cleaned), raw_first
    except json.JSONDecodeError:
        pass

    # 修复重试：只要求它把格式改对，不重新讨论任务拆解
    messages.append({"role": "assistant", "content": raw_first})
    messages.append({
        "role": "user",
        "content": "你上面的回答不是合法 JSON。请只输出一个 JSON 数组，不要任何解释、不要 markdown 代码块。",
    })

    raw_second = _call_llm(messages)
    cleaned = _strip_fence(raw_second)
    plan = json.loads(cleaned)     # 第二次仍失败则直接抛异常，不再重试

    raw = f"{raw_first}\n---repair---\n{raw_second}"
    return plan, raw


def _repair_message(problems: list) -> str:
    """把 validate_plan 挑出的具体问题拼成一条修复指令。

    不能只说"错了重来"——必须把每条问题原文列出来，
    模型才有信息去改，而不是重新赌一次。
    """
    lines = "\n".join(f"- {p}" for p in problems)
    return (
        f"上面的计划不合法：\n{lines}\n\n"
        "请修正后重新输出完整的 JSON 数组。只能使用给定的技能列表，只输出 JSON，不要任何解释。"
    )


def plan_and_repair(text: str, registry: dict, mode: str = "llm") -> tuple:
    """拆解 + 至多一轮"计划级"修复。

    流程：
        1. 调 plan_llm 拿到第一版计划
        2. validate_plan 校验，合法就直接返回
        3. 不合法：把 validate_plan 的具体问题列表回传给模型，
           追加进对话，重新问一次，产出第二版计划
        4. 不管第二版是否合法都原样返回，交给调用方（run.py /
           benchmarks）再校验一次，决定放不放行
        5. 修复上限硬编码为 1，不循环、不做成可配参数——
           无界重试会把延迟推高，这是刻意的设计约束

    这一轮和 plan_llm 内部已有的"JSON 格式修复"不是一回事：
    plan_llm 那一轮只管"这坨文本是不是合法 JSON"；
    这里管的是"这份 JSON 计划语义上站不站得住"（技能存不存在、
    参数全不全），本质是拿 validate_plan 的确定性判定去触发重新规划。

    与 ReAct 的区别（这是本函数存在的理由，别删）：
    ReAct 是每执行一步就回头让模型重新推理下一步该干什么，
    轮数不可预测、依赖执行时的真实状态、结果不可复现；
    这里执行阶段完全不回头问模型——规划依然是一次性产出的，
    只是当那一次产出没通过一个确定性的校验函数时，
    额外给一次"重新产出完整计划"的机会，次数固定为 1，
    跑两次就必须有结果，不会陷入不可控的多轮循环。

    返回 (plan, raw, repaired)：
        plan      最终计划（未必合法，调用方还要再校验一次）
        raw       各轮模型原文，用 \n---repair---\n 分隔；
                   没有触发计划级修复时只有一段，不含分隔符
        repaired  bool，是否触发过这里说的计划级修复
    """
    if mode != "llm":
        plan, raw = plan_mock(text, registry)
        return plan, raw, False

    plan, raw = plan_llm(text, registry)
    problems = validate_plan(plan, registry)
    if not problems:
        return plan, raw, False

    messages = [
        {
            "role": "system",
            "content": PLANNER_SYSTEM_PROMPT.format(skills=_format_skills(registry)),
        },
        {"role": "user", "content": text},
        {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
        {"role": "user", "content": _repair_message(problems)},
    ]

    raw_repair = _call_llm(messages)
    cleaned = _strip_fence(raw_repair)
    try:
        plan_repaired = json.loads(cleaned)
    except json.JSONDecodeError:
        plan_repaired = []     # 仍不合法：交回给调用方按空计划处理，不再重试

    combined_raw = f"{raw}\n---repair---\n{raw_repair}"
    return plan_repaired, combined_raw, True


def validate_plan(plan: list, registry: dict) -> list:
    """校验大模型输出。别信它，逐条查。

    返回问题列表，空列表表示计划合法。
    """
    known = {s["name"]: s for s in registry["skills"]}
    problems = []

    if not isinstance(plan, list):
        return ["计划不是数组"]

    for index, step in enumerate(plan, start=1):
        if not isinstance(step, dict) or "skill" not in step:
            problems.append(f"第 {index} 步格式不对")
            continue

        name = step["skill"]
        if name not in known:
            problems.append(f"第 {index} 步用了不存在的技能 {name}")
            continue

        args = step.get("args", {})
        for param, meta in known[name]["params"].items():
            if meta.get("required") and not args.get(param):
                problems.append(f"第 {index} 步 {name} 缺少必填参数 {param}")

    return problems
