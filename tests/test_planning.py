"""改版窗口试算、承诺保留、班组互斥与合同达标解释测试。"""
import unittest
from pathlib import Path

from weekly.coverage import evaluate_all
from weekly.model import load_bundle
from weekly.planning import WindowPlanner, discover_gap_work
from weekly.settlement import Settlement
from weekly.timeutil import parse_dt

DATA = Path(__file__).resolve().parent.parent / "data"
AS_OF = "2026-09-07T05:00"


class PlanningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA)
        cut = parse_dt(AS_OF)
        evals = evaluate_all(cls.bundle, cut)
        cls.settlement = Settlement(cls.bundle, evals, cut)
        cls.planner = WindowPlanner(cls.bundle, cls.settlement)

    def test_committed_windows_are_always_preserved(self):
        for plan_set in (self.planner.enumerate_plans(joint=False),
                         self.planner.enumerate_plans(joint=True)):
            for plan in plan_set:
                windows = {a.window.window for a in plan.assignments}
                self.assertIn("w1", windows)
                self.assertIn("w4", windows)
                committed = [a for a in plan.assignments
                             if a.window.window in ("w1", "w4")]
                for a in committed:
                    self.assertIsNone(a.work)

    def test_same_team_overlapping_windows_are_mutually_exclusive(self):
        # w1 与 w2 同属甲且时段重合；w1 已承诺 c1，任何方案都不能
        # 再把 w2 派给甲。
        for plan in self.planner.enumerate_plans(joint=True):
            for a in plan.assignments:
                if a.window.window == "w2":
                    self.assertNotEqual(a.team, "甲")

    def test_reassign_mode_keeps_team_within_window_crew(self):
        for plan in self.planner.enumerate_plans(joint=False):
            for a in plan.assignments:
                if a.work is not None:
                    self.assertEqual(a.team, a.window.same_team)

    def test_negative_evidence_work_is_not_governable(self):
        works = {w.work_id: w for w in discover_gap_work(self.settlement)}
        b1 = works[("b1", "东", "总糖已标")]
        self.assertFalse(b1.governable)
        self.assertIn("无声明", b1.block_reason)
        # 不可治理工作不得出现在任何窗口分配里。
        for plan in self.planner.enumerate_plans(joint=True):
            for a in plan.assignments:
                if a.work is not None:
                    self.assertNotEqual(a.work.work_id, b1.work_id)

    def test_shared_fact_work_serves_two_topics(self):
        works = {w.work_id: w for w in discover_gap_work(self.settlement)}
        shared = works[("a2", "东", "未加蔗糖")]
        self.assertEqual(set(shared.topics), {"T1", "T2"})

    def test_union_unit_needs_only_one_missing_work(self):
        # 乙在 w2 补 a2/东/总糖，即可让并集 T1 单元减少 4 个。
        best_joint = self.planner.recommend()["best_joint"]
        self.assertEqual(best_joint.expected_reduction["东"], 4)
        w2 = next(a for a in best_joint.assignments
                  if a.window.window == "w2")
        self.assertEqual(w2.work.work_id, ("a2", "东", "总糖已标"))
        self.assertEqual(w2.team, "乙")
        self.assertIn("跨班组合做", w2.reason)

    def test_in_transit_claim_can_close_south_gap_after_confirmation(self):
        # w3 派丙做 c1/南（c4 在途确认后），南区可减少 17 个页面单元。
        best_joint = self.planner.recommend()["best_joint"]
        self.assertEqual(best_joint.expected_reduction["南"], 17)

    def test_shortfalls_explain_window_vs_fact_shortage(self):
        rec = self.planner.recommend()
        notes = " ".join(rec["shortages"])
        self.assertIn("东缺窗口", notes)
        self.assertIn("南缺窗口", notes)
        # 两个区都达不到合同最低覆盖。
        for plan in (rec["best_reassign"], rec["best_joint"]):
            self.assertFalse(all(plan.meets_standard.values()))

    def test_only_real_windows_are_assigned(self):
        real = {w.window for w in self.bundle.windows}
        for plan in self.planner.enumerate_plans(joint=True):
            for a in plan.assignments:
                self.assertIn(a.window.window, real)

    def test_capacity_one_one_work_per_window(self):
        for plan in self.planner.enumerate_plans(joint=True):
            used = [a.window.window for a in plan.assignments
                    if a.work is not None]
            self.assertEqual(len(used), len(set(used)))


if __name__ == "__main__":
    unittest.main()
