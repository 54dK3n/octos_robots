#!/usr/bin/env python3
"""基准脚本：对 20 条指令跑 planner，统计 raw / final 两个成功率，
外加一组独立的 validate_plan 单元检查（"校验层"）。

raw   —— 只看模型第一次输出（未经计划级修复）能不能用
final —— 走完整流程之后能不能用（llm 模式下含至多一次计划级修复）

mock 模式没有修复这一说（plan_and_repair 对 mock 直接短路返回），
raw == final 恒成立，这是预期行为，不是 bug——
mock 只是规则拆解，没有"模型输出、校验失败、回传错误重新问"这个环节。
raw/final 的差距只有在 --planner llm 时才有意义。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import planner as planner_module  # noqa: E402

REGISTRY_PATH = ROOT / "skills" / "registry.json"
CASES_PATH = Path(__file__).resolve().parent / "cases.json"

# 5 份手写的错误计划，直接喂给 validate_plan，跟 planner 模式无关。
# 用来回答"校验器这道闸门本身还灵不灵"，不参与 raw/final 统计。
VALIDATOR_CASES = [
    {"id": "v01", "desc": "技能名编造", "plan": [{"skill": "goto", "args": {"target": "球"}}]},
    {"id": "v02", "desc": "缺必填参数 target", "plan": [{"skill": "place", "args": {}}]},
    {"id": "v03", "desc": "必填参数为空字符串", "plan": [{"skill": "pick", "args": {"object": ""}}]},
    {"id": "v04", "desc": "步骤缺 skill 字段", "plan": [{"args": {"target": "球"}}]},
    {"id": "v05", "desc": "计划根本不是数组", "plan": {"skill": "move_to", "args": {"target": "球"}}},
]


def load_registry() -> dict:
    with open(REGISTRY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def load_cases() -> list:
    with open(CASES_PATH, encoding="utf-8") as handle:
        return json.load(handle)["cases"]


def parse_first_attempt(raw: str) -> list:
    """从 raw 里切出"模型第一次输出"对应的计划，用来算 raw 指标。

    plan_and_repair 把多轮原文用 \n---repair---\n 拼在一起，
    不管后面发生了几轮修复，第一段永远是模型第一次给出的原始回答，
    这正是 raw 指标要看的东西——按这个分隔符切一刀就行，不用动。
    """
    if not raw:
        return []
    first_segment = raw.split("\n---repair---\n")[0]
    cleaned = planner_module._strip_fence(first_segment)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return []


def skill_sequence(plan):
    """把计划抽成技能名序列，形状不对就返回 None。"""
    if not isinstance(plan, list):
        return None
    seq = []
    for step in plan:
        if not isinstance(step, dict) or "skill" not in step:
            return None
        seq.append(step["skill"])
    return seq


def judge_case(case: dict, plan, registry: dict) -> bool:
    """按用例的 ground truth 判定这份计划算不算"对"。

    solvable   —— 技能序列要跟 expected_skills 完全一致，
                  并且这份计划本身要能通过 validate_plan
                  （只比技能序列不查 validate_plan 会算高分：
                  一个 pick 少了 object 参数，序列照样对得上，
                  但拿去 run.py 跑就会被拦下来，不能算"对"）。
                  不比对参数字面量的具体取值——validate_plan 本来
                  就不做语义校验，这是当前系统的真实能力边界。
    unsolvable —— 正确行为是老老实实输出空计划 []。
    """
    if case["expect"] == "unsolvable":
        return plan == []
    if skill_sequence(plan) != case.get("expected_skills"):
        return False
    return not planner_module.validate_plan(plan, registry)


def call_planner(text: str, registry: dict, mode: str) -> tuple:
    """统一出口：mock 直接拆，llm 走一次计划级修复。

    归一成 (plan, raw, repaired) 三元组，跟
    orchestrator.planner.plan_and_repair 的返回形态对齐。
    """
    if mode == "mock":
        plan, raw = planner_module.plan_mock(text, registry)
        return plan, raw, False
    return planner_module.plan_and_repair(text, registry, mode="llm")


def run_validator_layer(registry: dict) -> dict:
    """独立于 planner，直接拿 5 份手写的错误计划考 validate_plan。"""
    caught = 0
    detail = []
    for item in VALIDATOR_CASES:
        problems = planner_module.validate_plan(item["plan"], registry)
        ok = bool(problems)
        caught += int(ok)
        detail.append({"id": item["id"], "desc": item["desc"], "caught": ok, "problems": problems})
    return {"caught": caught, "total": len(VALIDATOR_CASES), "detail": detail}


def run_bench(mode: str, registry: dict, cases: list) -> dict:
    results = []
    for case in cases:
        error = None
        try:
            plan, raw, repaired = call_planner(case["text"], registry, mode)
        except Exception as exc:      # noqa: BLE001
            plan, raw, repaired, error = [], "", False, str(exc)

        raw_plan = parse_first_attempt(raw)
        raw_ok = judge_case(case, raw_plan, registry)
        final_ok = judge_case(case, plan, registry) if error is None else False

        results.append({
            "id": case["id"],
            "text": case["text"],
            "expect": case["expect"],
            "repaired": repaired,
            "raw_ok": raw_ok,
            "final_ok": final_ok,
            "flipped_by_repair": bool(repaired and (not raw_ok) and final_ok),
            "error": error,
        })

    return {
        "mode": mode,
        "total": len(results),
        "raw_score": sum(r["raw_ok"] for r in results),
        "final_score": sum(r["final_ok"] for r in results),
        "repaired_count": sum(r["repaired"] for r in results),
        "flipped_ids": [r["id"] for r in results if r["flipped_by_repair"]],
        "results": results,
    }


def print_report(report: dict, validator: dict) -> None:
    print("=" * 60)
    print(f"planner = {report['mode']}")
    print(f"raw   {report['raw_score']}/{report['total']}")
    print(f"final {report['final_score']}/{report['total']}")
    print(f"触发计划级修复：{report['repaired_count']} 条")
    if report["flipped_ids"]:
        print(f"修复挽回的用例：{', '.join(report['flipped_ids'])}")
    print("-" * 60)
    for r in report["results"]:
        mark = "OK" if r["final_ok"] else "XX"
        tag = "  [REPAIR]" if r["repaired"] else ""
        err = f"  error={r['error']}" if r["error"] else ""
        print(f"  [{mark}] {r['id']} raw={'OK' if r['raw_ok'] else 'XX'} final={'OK' if r['final_ok'] else 'XX'}{tag}{err}  {r['text']}")
    print("-" * 60)
    print(f"校验层（validate_plan 单测）：{validator['caught']}/{validator['total']}")
    for item in validator["detail"]:
        mark = "OK" if item["caught"] else "XX"
        print(f"  [{mark}] {item['id']} {item['desc']}")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="拆解基准：raw vs final")
    parser.add_argument("--planner", choices=["mock", "llm"], default="mock")
    parser.add_argument("--repeat", type=int, default=1, help="重复跑几遍，用于看 llm 输出复不复现")
    parser.add_argument("--out", help="把 JSON 报告写到这个路径，不填就只打印")
    args = parser.parse_args()

    registry = load_registry()
    cases = load_cases()
    validator = run_validator_layer(registry)

    runs = []
    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"\n######## 第 {i + 1}/{args.repeat} 轮 ########")
        report = run_bench(args.planner, registry, cases)
        print_report(report, validator)
        runs.append(report)

    if args.repeat > 1:
        # 复现一致性：同一条用例在每一轮里 final_ok 是不是都一样
        consistent = 0
        for idx in range(len(cases)):
            outcomes = {run["results"][idx]["final_ok"] for run in runs}
            if len(outcomes) == 1:
                consistent += 1
        print(f"\n复现一致：{consistent}/{len(cases)}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"runs": runs, "validator": validator}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n报告已写入 {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
