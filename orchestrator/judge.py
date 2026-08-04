"""Judge —— 判定"这一步到底成没成"。

现在是 mock：直接采信技能自己的报告。
以后接真视觉：这里换成调检测模型，看图说话。

契约固定死，不许改：
    {"success": bool, "detail": str, "evidence": dict}

为什么要单独一层、不直接用技能的返回值？
因为技能说自己成了不算数。手臂电机跑到位了不等于球真被抓起来了。
留这一层，以后才有地方插视觉判定。
"""


def judge(step: dict, result: dict) -> dict:
    """判定单步结果。

    step   本步的计划，例如 {"skill": "pick", "args": {...}}
    result 技能的原始返回
    """
    return {
        "success": bool(result.get("success")),
        "detail": result.get("detail", ""),
        "evidence": result.get("evidence", {}),
    }
