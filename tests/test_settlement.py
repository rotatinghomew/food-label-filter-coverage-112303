"""认领结算、共同贡献、外溢、在途与快照争议测试。"""
import unittest
from pathlib import Path

from weekly.coverage import evaluate_all
from weekly.model import load_bundle
from weekly.settlement import JOINT, Settlement, diff_snapshots
from weekly.timeutil import parse_dt

DATA = Path(__file__).resolve().parent.parent / "data"
AS_OF = "2026-09-07T05:00"
OLD_CUT = "2026-08-31T13:00"


class SettlementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA)
        cls.cut = parse_dt(AS_OF)
        cls.evals = evaluate_all(cls.bundle, cls.cut)
        cls.settlement = Settlement(cls.bundle, cls.evals, cls.cut)

    def test_topic_attainment_excludes_spillover(self):
        attainment = self.settlement.topic_attainment()
        # T1 没有登记给 T1 的确认认领（c1 已被撤回），达成度为 0，
        # 乙的共用事实外溢单列，不能充 T1 目标。
        self.assertEqual(attainment["T1"]["attainment"], 0.0)
        self.assertGreaterEqual(attainment["T1"]["spillover"], 1)
        # 地区拆键后 T2 为 54 个专题单元（东+南）。
        self.assertEqual(attainment["T2"]["units"], 54)
        self.assertEqual(attainment["T2"]["credited"], 17)
        self.assertEqual(attainment["T2"]["attainment"],
                         round(17 / 54, 4))
        # T3 全部缺口，c4 未确认只能算在途。
        self.assertEqual(attainment["T3"]["credited"], 0)
        self.assertEqual(attainment["T3"]["in_transit"], 17)

    def test_group_reduction_dedups_across_topics_and_respects_cap(self):
        group = self.settlement.group_reduction()
        self.assertEqual(group["joint_reduced_page_units"],
                         group["team_credit_sum"])
        self.assertTrue(group["cap_respected"])
        # 乙在 T1 外溢 + T2 本认领，页面去重后联合减少 26。
        self.assertEqual(group["joint_reduced_page_units"], 26)

    def test_c4_remains_in_transit_not_target_credit(self):
        ledger_bing = self.settlement.ledgers["丙"]
        self.assertIn("c4", ledger_bing.in_transit_claims)
        self.assertEqual(len(ledger_bing.credit_units), 0)

    def test_old_snapshot_joint_credit_on_e1(self):
        old_evals = evaluate_all(self.bundle, parse_dt(OLD_CUT))
        old = Settlement(self.bundle, old_evals, parse_dt(OLD_CUT))
        unit = old.units[("甲", "s00", "p1", "T2", "东")]
        self.assertEqual(unit.status, "可筛")
        teams = set(unit.credits)
        self.assertEqual(teams, {"甲", "乙"})
        for team, (claim, shape, spillover) in unit.credits.items():
            self.assertEqual(shape, JOINT)
        # c1 登记在 T1，对 T2 是跨专题外溢；c2 是 T2 本认领。
        self.assertTrue(unit.credits["甲"][2])
        self.assertFalse(unit.credits["乙"][2])

    def test_correction_reopens_intersection_gap_and_holds_other_sessions(self):
        old_evals = evaluate_all(self.bundle, parse_dt(OLD_CUT))
        old = Settlement(self.bundle, old_evals, parse_dt(OLD_CUT))
        diff = diff_snapshots(old, self.settlement)
        reopened_claims = {(r["unit"], r["claim"]) for r in diff["reopened"]}
        # e1 所在交集单元的共同贡献整笔重审，c1 与 c2 两笔都列争议。
        self.assertIn((("甲", "s00", "p1", "T2", "东"), "c1"), reopened_claims)
        self.assertIn((("甲", "s00", "p1", "T2", "东"), "c2"), reopened_claims)
        # 8 个东区交集单元 × 2 笔旧贡献 = 16 条重开记录。
        self.assertEqual(len(diff["reopened"]), 16)
        # 乙在其他会话（南区 c3、并集 v2）的单独贡献继续算，未被撤销。
        yi = self.settlement.ledgers["乙"]
        self.assertEqual(len(yi.sole_units), 28)
        # 甲的旧功劳只在 T2 交集（8 个单元）；并集 T1 同刻单元单独归乙，
        # c1 撤回不影响这些单元，故甲争议单元恰为 8。
        jia = self.settlement.ledgers["甲"]
        self.assertEqual(len(jia.credit_units), 0)
        self.assertEqual(len(jia.disputed_units), 8)

    def test_union_self_heal_needs_no_rework(self):
        old_evals = evaluate_all(self.bundle, parse_dt(OLD_CUT))
        old = Settlement(self.bundle, old_evals, parse_dt(OLD_CUT))
        diff = diff_snapshots(old, self.settlement)
        # 并集 T1 单元同刻治理时单独归乙（c2），c1 从未计入 T1 功劳；
        # 因此 c1 撤回后 T1 既不重开也不改归，没有重复派活。
        reopened_topics = {r["topic"] for r in diff["reopened"]}
        self.assertNotIn("T1", reopened_topics)
        t1_units = [u for k, u in self.settlement.units.items()
                    if k[3] == "T1"]
        # 仍可筛的 T1 单元继续由乙的外溢事实单独支撑。
        still_filterable = [u for u in t1_units if not u.is_gap]
        self.assertTrue(still_filterable)
        for unit in still_filterable:
            self.assertTrue(all(t == "乙" for t in unit.credits))

    def test_disputed_units_are_recorded_on_current_snapshot(self):
        old_evals = evaluate_all(self.bundle, parse_dt(OLD_CUT))
        old = Settlement(self.bundle, old_evals, parse_dt(OLD_CUT))
        diff_snapshots(old, self.settlement)
        e1 = self.settlement.units[("甲", "s00", "p1", "T2", "东")]
        self.assertIn("c1", e1.disputed_claims)
        self.assertIn("c2", e1.disputed_claims)

    def test_claim_only_counts_for_its_effective_region_and_conditions(self):
        # c3 是 a2/南/总糖，绝不能计入东区单元。
        for unit in self.settlement.units.values():
            if unit.region != "南":
                for team, (claim_id, _shape, _sp) in unit.credits.items():
                    self.assertNotEqual(claim_id, "c3")


if __name__ == "__main__":
    unittest.main()
