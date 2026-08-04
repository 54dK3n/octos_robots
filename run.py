#!/usr/bin/env python3
"""入口。一句话进来，一串技能调用跑完。

    python3 run.py "把地上的球捡起来放桌上"
    python3 run.py "把球捡起来" --retries 2
    python3 run.py "把地上的球捡起来放桌上" --planner llm
    python3 run.py "把地上的球捡起来放桌上" --planner llm --dry-run
    python3 run.py --list-skills
"""
import argparse
import json
import sys
from pathlib import Path

from orchestrator import planner as planner_module
from orchestrator.executor import Executor

ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "skills" / "registry.json"
EVIDENCE_PATH = ROOT / "reports" / "evidence" / "last-raw.txt"


def load_registry() -> dict:
    with open(REGISTRY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def save_raw_evidence(raw: str) -> None:
    """把 planner 的原始输出留存下来，供事后统计正确率用。"""
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(raw, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="轮足双臂机器人 · 任务编排层 demo")
    parser.add_argument("text", nargs="?", help="中文指令")
    parser.add_argument("--planner", choices=["mock", "llm"], default="mock",
                        help="mock=规则拆解（默认，不联网）；llm=真调大模型")
    parser.add_argument("--retries", type=int, default=1, help="每步最多重试几次")
    parser.add_argument("--list-skills", action="store_true", help="列出已注册技能")
    parser.add_argument("--dry-run", action="store_true", help="只拆解和校验，不执行")
    args = parser.parse_args()

    registry = load_registry()

    if args.list_skills:
        for skill in registry["skills"]:
            params = ", ".join(skill["params"])
            print(f"{skill['name']}({params})  [{skill['category']}]  归属：{skill['owner']}")
            print(f"    {skill['description']}")
        return 0

    if not args.text:
        parser.print_help()
        return 1

    print("=" * 60)
    print(f"用户指令：{args.text}")
    print("=" * 60)

    # ---------- 第一步：拆解 ----------
    print(f"\n【PLAN】用 {args.planner} planner 拆解")
    try:
        if args.planner == "llm":
            # llm 模式下带一次计划级修复：校验不过就把问题回传给模型重规划一次，上限 1 次
            plan, raw, repaired = planner_module.plan_and_repair(args.text, registry, mode="llm")
        else:
            # mock 模式不走修复，直接返回
            plan, raw = planner_module.plan_mock(args.text, registry)
            repaired = False
    except Exception as error:      # noqa: BLE001
        print(f"  拆解失败：{error}")
        return 1

    save_raw_evidence(raw)
    if repaired:
        print("【REPAIR】计划不合法，已重新规划一次")
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    if not plan:
        print("\n拆不出步骤。这条指令要么超出当前技能范围，要么该进澄清流程。")
        return 1

    # ---------- 第二步：校验（修复过也要在这里再查一遍，不能白名单放行） ----------
    problems = planner_module.validate_plan(plan, registry)
    if problems:
        print("\n【VALIDATE】计划不合法，拒绝执行：")
        for problem in problems:
            print(f"  - {problem}")
        if repaired:
            print("  （已经修复过一次，不再重试）")
        return 1
    print("\n【VALIDATE】通过")

    if args.dry_run:
        print("\n--dry-run：只拆解校验，不执行")
        return 0

    # ---------- 第三步：执行 ----------
    print("\n【EXECUTE】开始逐步执行")
    executor = Executor(registry, max_retries=args.retries)
    outcome = executor.run(plan)

    print("\n" + "=" * 60)
    print(f"结果：{'成功' if outcome['success'] else '失败'} —— {outcome['detail']}")
    print(f"完成步数：{outcome['completed']} / {len(plan)}")
    print(f"总尝试次数：{len(executor.log)}")
    print("=" * 60)

    return 0 if outcome["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
