#!/usr/bin/env python3
"""对 plan_and_repair 的修复分支做单测。

不接真实网络——用一个假的 _call_llm 顶替掉真实请求：
第一次返回一份用了不存在技能 goto 的计划，第二次返回一份合法计划，
确认 plan_and_repair 真的走了修复分支、repaired=True、
且最终计划能通过 validate_plan、修复消息里带上了具体错误原因。

这份测试要长期留着，不是跑一次就删——上一阶段吃过"一次性验证
跑完就扔、后面没法回归"的亏，这次单测按可重复执行的方式落盘。

跑法：
    python3 benchmarks/test_repair.py
    python3 -m unittest benchmarks.test_repair
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import planner as planner_module  # noqa: E402


class FakeLLM:
    """按调用次数依次吐出预设文本，模拟"先错后对"的两轮对话。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeLLM 被调用的次数超出预设")
        return self.responses.pop(0)


def _test_registry() -> dict:
    return {
        "skills": [
            {
                "name": "move_to",
                "description": "移动到目标附近",
                "params": {"target": {"type": "string", "required": True}},
            },
            {
                "name": "pick",
                "description": "抓取物体",
                "params": {"object": {"type": "string", "required": True}},
            },
            {
                "name": "place",
                "description": "放置物体",
                "params": {"target": {"type": "string", "required": True}},
            },
        ]
    }


class PlanAndRepairTest(unittest.TestCase):
    def setUp(self):
        self.registry = _test_registry()
        self._original_call_llm = planner_module._call_llm

    def tearDown(self):
        planner_module._call_llm = self._original_call_llm

    def test_repair_branch_fixes_invalid_skill(self):
        """第一次输出编造技能名 goto，第二次才给出合法计划。"""
        bad_plan = json.dumps(
            [{"skill": "goto", "args": {"target": "球"}}], ensure_ascii=False
        )
        good_plan = json.dumps(
            [
                {"skill": "move_to", "args": {"target": "球"}},
                {"skill": "pick", "args": {"object": "球"}},
                {"skill": "move_to", "args": {"target": "桌子"}},
                {"skill": "place", "args": {"target": "桌子"}},
            ],
            ensure_ascii=False,
        )
        fake = FakeLLM([bad_plan, good_plan])
        planner_module._call_llm = fake

        plan, raw, repaired = planner_module.plan_and_repair(
            "把地上的球捡起来放桌上", self.registry, mode="llm"
        )

        self.assertTrue(repaired)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(planner_module.validate_plan(plan, self.registry), [])
        self.assertIn("\n---repair---\n", raw)

        # 修复消息必须把具体错误告诉模型，不能只说"错了重来"
        repair_prompt = fake.calls[1][-1]["content"]
        self.assertIn("goto", repair_prompt)

    def test_valid_first_attempt_skips_repair(self):
        """第一次就合法时不该触发第二轮调用。"""
        good_plan = json.dumps(
            [{"skill": "move_to", "args": {"target": "门口"}}], ensure_ascii=False
        )
        fake = FakeLLM([good_plan])
        planner_module._call_llm = fake

        plan, raw, repaired = planner_module.plan_and_repair(
            "移动到门口", self.registry, mode="llm"
        )

        self.assertFalse(repaired)
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn("---repair---", raw)
        self.assertEqual(plan, json.loads(good_plan))

    def test_second_attempt_still_invalid_returns_as_is(self):
        """修复上限固定为 1：第二次仍不合法就原样交回，不再重试。"""
        bad_plan = json.dumps(
            [{"skill": "goto", "args": {"target": "球"}}], ensure_ascii=False
        )
        still_bad_plan = json.dumps(
            [{"skill": "goto", "args": {"target": "桌子"}}], ensure_ascii=False
        )
        fake = FakeLLM([bad_plan, still_bad_plan])
        planner_module._call_llm = fake

        plan, raw, repaired = planner_module.plan_and_repair(
            "把地上的球捡起来放桌上", self.registry, mode="llm"
        )

        self.assertTrue(repaired)
        self.assertEqual(len(fake.calls), 2)     # 没有第三次调用
        self.assertNotEqual(planner_module.validate_plan(plan, self.registry), [])

    def test_mock_mode_never_repairs(self):
        """mock 模式直接短路，不碰 _call_llm。"""
        planner_module._call_llm = FakeLLM([])   # 一次都不该被调用

        plan, raw, repaired = planner_module.plan_and_repair(
            "把球捡起来", self.registry, mode="mock"
        )

        self.assertFalse(repaired)
        self.assertNotIn("---repair---", raw)


if __name__ == "__main__":
    unittest.main()
