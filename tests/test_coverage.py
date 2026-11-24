"""覆盖判定、并/交逻辑、去重口径与版本切换测试。"""
import unittest
from pathlib import Path

from weekly.catalog import Catalog
from weekly.coverage import (
    FILTERABLE, NO_SELLABLE, UNDETERMINED, BLANK, NEGATIVE,
    attribute_share, evaluate_all,
    filter_evaluations, page_view_key, summarize, topic_view_key,
)
from weekly.model import load_bundle
from weekly.timeutil import parse_dt

DATA = Path(__file__).resolve().parent.parent / "data"
AS_OF = "2026-09-07T05:00"
OLD_CUT = "2026-08-31T13:00"


class CoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA)
        cls.evals = evaluate_all(cls.bundle, parse_dt(AS_OF))
        cls.by_event = {e.exposure.event: e for e in cls.evals}

    def test_page_dedup_matches_independent_crosscheck(self):
        summary = summarize(self.evals, page_view_key)
        # 地区维度保留后，页面去重单元 120 个（原跨地区合并会虚低）。
        self.assertEqual(summary.denominator, 120)
        self.assertEqual(summary.counts[FILTERABLE], 39)
        self.assertEqual(summary.counts[NO_SELLABLE], 10)
        self.assertEqual(summary.counts[UNDETERMINED], 39)
        self.assertEqual(summary.counts[BLANK], 5)
        self.assertEqual(summary.counts[NEGATIVE], 27)

    def test_topic_dedup_keeps_its_own_count(self):
        t2 = filter_evaluations(self.evals, topic="T2")
        summary = summarize(t2, topic_view_key)
        self.assertEqual(summary.denominator, 54)
        self.assertEqual(summary.counts[FILTERABLE], 17)
        self.assertEqual(summary.counts[UNDETERMINED], 24)
        self.assertEqual(summary.counts[BLANK], 13)

    def test_single_attribute_shares(self):
        sugar_free = attribute_share(self.evals, "未加蔗糖", page_view_key)
        total_sugar = attribute_share(self.evals, "总糖已标", page_view_key)
        self.assertEqual(sugar_free.counts[FILTERABLE], 20)
        self.assertEqual(total_sugar.counts[FILTERABLE], 32)
        # 否定证据不得在单声明口径里被算作资料空白。
        self.assertEqual(total_sugar.counts[NEGATIVE], 27)
        self.assertEqual(total_sugar.counts[BLANK], 16)

    def test_e0_union_filterable_through_either_claim(self):
        ev = self.by_event["e0"]
        self.assertEqual(ev.rule.logic, "并")
        # 新截面未加蔗糖被撤回，但并集下总糖已标仍支持，单元保持可筛。
        self.assertEqual(ev.status, FILTERABLE)
        self.assertFalse(ev.attribute_supported("未加蔗糖"))
        self.assertTrue(ev.attribute_supported("总糖已标"))
        # 旧截面两个条件都支持。
        old = evaluate_all(self.bundle, parse_dt(OLD_CUT))
        old_e0 = next(e for e in old if e.exposure.event == "e0")
        self.assertTrue(old_e0.attribute_supported("未加蔗糖"))
        self.assertTrue(old_e0.attribute_supported("总糖已标"))

    def test_e1_intersection_requires_both_on_same_sku(self):
        # 旧截面 f3 仍支持：交集两边都成立。
        old = evaluate_all(self.bundle, parse_dt(OLD_CUT))
        old_e1 = next(e for e in old if e.exposure.event == "e1")
        self.assertEqual(old_e1.rule.logic, "交")
        self.assertEqual(old_e1.status, FILTERABLE)
        # 新截面 f3 被撤回为未知：交集整单不可筛，归证据未判定。
        new_e1 = self.by_event["e1"]
        self.assertEqual(new_e1.status, UNDETERMINED)

    def test_multi_select_union_vs_intersection_on_same_facts(self):
        # 新截面 a2/东：未加蔗糖=未知、总糖已标=支持。
        # 同一份事实下，并集（T1 v1 / T2 v2）可筛，交集（T2 v1）不可筛，
        # 证明多条件含义由当时界面配置决定，而非固定。
        union_ev = self.by_event["e0"]   # T1 并
        intersect_ev = self.by_event["e1"]  # T2 交
        self.assertEqual(union_ev.rule.logic, "并")
        self.assertEqual(intersect_ev.rule.logic, "交")
        self.assertEqual(union_ev.status, FILTERABLE)
        self.assertEqual(intersect_ev.status, UNDETERMINED)
        # 交集必须在“同一规格”上同时满足，不能跨规格拼凑。
        for se in intersect_ev.sku_evaluations:
            self.assertFalse(se.matches)

    def test_e3_negative_evidence_blocks(self):
        ev = self.by_event["e3"]
        self.assertEqual(ev.status, NEGATIVE)

    def test_e4_unknown_is_undetermined_blank_before_registration(self):
        ev = self.by_event["e4"]
        self.assertEqual(ev.status, UNDETERMINED)

    def test_no_sellable_sku_status(self):
        # p3 南区窗口 08-27 才开始；e24（08-24）落在窗外，归无可售规格。
        target = self.by_event["e24"]
        self.assertEqual(target.exposure.page, "p3")
        self.assertEqual(target.exposure.region, "南")
        self.assertEqual(target.status, NO_SELLABLE)
        # 开窗后的同页曝光（如 e69，08-29）则有可售规格 c1。
        after = self.by_event["e69"]
        self.assertEqual(after.status, UNDETERMINED)
        self.assertTrue(after.sku_evaluations)

    def test_topic_version_switch_picks_rule_at_exposure_time(self):
        catalog = Catalog(self.bundle)
        before = catalog.rule_at("T2", "p1", parse_dt("2026-08-30T23:59"))
        after = catalog.rule_at("T2", "p1", parse_dt("2026-08-31T00:00"))
        self.assertEqual((before.version, before.logic), (1, "交"))
        self.assertEqual((after.version, after.logic), (2, "并"))

    def test_conditions_subset_of_topic_config_is_allowed(self):
        # e7 在 v2 并集下只选了总糖已标，不应报错。
        ev = self.by_event["e7"]
        self.assertEqual(ev.exposure.conditions, ("总糖已标",))
        self.assertEqual(ev.rule.version, 2)

    def test_drill_down_exposes_sku_windows_and_facts(self):
        drill = self.by_event["e1"].drill()
        self.assertEqual(drill["skus"][0]["sku"], "a2")
        self.assertEqual(drill["skus"][0]["window"],
                         ["2026-08-28T00:00:00", "2026-09-08T00:00:00"])
        self.assertIn("未加蔗糖", drill["skus"][0]["attributes"])

    def test_family_recode_a1_to_a2(self):
        catalog = Catalog(self.bundle)
        timeline = catalog.family_timeline("A")
        skus = {w.sku for w in timeline}
        self.assertEqual(skus, {"a1", "a2"})
        # 换码时点：东区 a1 窗右端 = a2 窗左端。
        east = sorted((w for w in timeline if w.region == "东"),
                      key=lambda w: w.begin)
        self.assertEqual(east[0].end, east[1].begin)

    def test_filter_drill_by_region_and_status(self):
        subset = filter_evaluations(self.evals, region="南",
                                    status=UNDETERMINED)
        self.assertTrue(subset)
        for ev in subset:
            self.assertEqual(ev.exposure.region, "南")
            self.assertEqual(ev.status, UNDETERMINED)


if __name__ == "__main__":
    unittest.main()
