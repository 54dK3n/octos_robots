"""Executor —— 把步骤列表一步步跑完。

状态机：DISPATCH → EXECUTE → JUDGE → RETRY / REPORT

这是整个编排层的心脏，也是最不该炫技的地方。越死板越好。
它跟 Octos 没有任何关系，将来换 agent_vla 也不用重写。
"""
import json
import subprocess
from pathlib import Path

from . import judge as judge_module

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class Executor:
    def __init__(self, registry: dict, max_retries: int = 1, verbose: bool = True):
        self.registry = {s["name"]: s for s in registry["skills"]}
        self.max_retries = max_retries
        self.verbose = verbose
        self.log = []          # 结构化 attempt 日志，用来做基准和复盘

    def _say(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _dispatch(self, step: dict) -> tuple:
        """DISPATCH：查表、校验参数。查不到就地失败，不往下走。"""
        name = step.get("skill")
        skill = self.registry.get(name)

        if skill is None:
            return None, f"未注册的技能 {name}"

        args = step.get("args", {})
        for param, meta in skill["params"].items():
            if meta.get("required") and not args.get(param):
                return None, f"{name} 缺少必填参数 {param}"

        return skill, None

    def _execute(self, name: str, args: dict, attempt: int) -> dict:
        """EXECUTE：真去调那个技能脚本。"""
        entry = SKILLS_DIR / name / "main"
        payload = dict(args)
        payload["attempt"] = attempt   # 让技能知道这是第几次尝试

        try:
            completed = subprocess.run(
                [str(entry), name],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return {"success": False, "detail": f"技能脚本不存在：{entry}", "evidence": {}}
        except subprocess.TimeoutExpired:
            return {"success": False, "detail": f"{name} 执行超时", "evidence": {}}

        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {
                "success": False,
                "detail": f"{name} 返回的不是合法 JSON：{completed.stdout[:200]}",
                "evidence": {},
            }

    def run_step(self, index: int, step: dict) -> dict:
        """跑单步，含重试。返回最终判定。"""
        name = step.get("skill")
        args = step.get("args", {})

        self._say(f"\n[第 {index} 步] {name}({json.dumps(args, ensure_ascii=False)})")

        # --- DISPATCH ---
        _, error = self._dispatch(step)
        if error:
            self._say(f"  DISPATCH 失败：{error}")
            verdict = {"success": False, "detail": error, "evidence": {}}
            self.log.append({"step": index, "attempt": 0, "verdict": verdict})
            return verdict

        # --- EXECUTE → JUDGE → RETRY ---
        for attempt in range(1, self.max_retries + 2):   # 首次 + max_retries 次重试
            self._say(f"  EXECUTE  第 {attempt} 次尝试")
            result = self._execute(name, args, attempt)

            verdict = judge_module.judge(step, result)
            self._say(f"  JUDGE    success={verdict['success']}  {verdict['detail']}")
            self.log.append({"step": index, "attempt": attempt, "verdict": verdict})

            if verdict["success"]:
                return verdict

            if attempt <= self.max_retries:
                self._say("  RETRY    重试")

        self._say(f"  REPORT   第 {index} 步放弃")
        return verdict

    def run(self, plan: list) -> dict:
        """跑完整个计划。任何一步最终失败就整体中止。"""
        if not plan:
            return {"success": False, "detail": "计划为空，没有可执行的步骤", "completed": 0}

        for index, step in enumerate(plan, start=1):
            verdict = self.run_step(index, step)
            if not verdict["success"]:
                return {
                    "success": False,
                    "detail": f"第 {index} 步失败：{verdict['detail']}",
                    "completed": index - 1,
                }

        return {"success": True, "detail": "全部步骤完成", "completed": len(plan)}
