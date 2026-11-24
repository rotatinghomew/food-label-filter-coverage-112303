"""周结装配、旧截面保留与叙述输出的端到端测试。"""
import json
import unittest
from pathlib import Path

from weekly.cli import render_markdown
from weekly.model import load_bundle
from weekly.report import build_report

DATA = Path(__file__).resolve().parent.parent / "data"


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA)
        cls.report = build_report(cls.bundle, "2026-09-07T05:00")
        cls.d = cls.report.to_dict()

    def test_report_is_json_serializable(self):
        text = json.dumps(self.d, ensure_ascii=False)
        self.assertIn("2026-W37", text)

    def test_old_close_snapshot_is_preserved(self):
        old = self.d["snapshot_diff"]["old_snapshot"]
        self.assertEqual(old["known_through"], "2026-08-31T13:00:00")
        self.assertEqual(old["week"], ["2026-W35"])

    def test_three_required_shares_plus_negative_are_reported(self):
        counts = self.d["coverage_overall"]["counts"]
        for label in ("可筛", "证据未判定", "当周资料空白", "已判定无声明",
                      "无可售规格"):
            self.assertIn(label, counts)
        # 四类份额相加为 1（无可售规格独立列示）。
        shares = self.d["coverage_overall"]["shares"]
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=3)

    def test_root_cause_splits_two_reasons(self):
        rc = self.d["root_cause"]
        self.assertEqual(rc["无可售规格"], 10)
        self.assertEqual(rc["可售但不可筛"], 71)
        self.assertEqual(
            rc["可筛"] + rc["无可售规格"] + rc["可售但不可筛"],
            rc["deduped_page_units"],
        )
        self.assertEqual(rc["deduped_page_units"], 120)

    def test_snapshot_diff_explains_reopened_gap(self):
        reopened = self.d["snapshot_diff"]["reopened"]
        self.assertTrue(reopened)
        record = next(r for r in reopened if r["claim"] == "c1")
        self.assertIn("重新打开", record["reason"])
        self.assertEqual(record["new_status"], "证据未判定")

    def test_topic_version_split_flags_comparable_scope(self):
        split = self.d["topic_versions"]["T2"]
        self.assertEqual(len(split), 2)
        for row in split:
            self.assertIn("可比", row["comparable"])

    def test_family_continuity_tracks_recode(self):
        family_a = next(f for f in self.d["family_continuity"]
                        if f["family"] == "A")
        skus = {t["sku"] for t in family_a["timeline"]}
        self.assertEqual(skus, {"a1", "a2"})

    def test_markdown_contains_all_sections(self):
        md = render_markdown(self.report)
        for heading in (
            "覆盖实况", "专题口径", "归功与目标达成", "开放缺口与在途",
            "归属更正与旧账重审", "改版窗口建议", "同配方换码对照",
        ):
            self.assertIn(heading, md)
        # 旧账里的争议认领号要能在叙述中追溯。
        self.assertIn("c1", md)
        self.assertIn("c4", md)

    def test_in_transit_does_not_count_as_target(self):
        for item in self.d["in_transit"]:
            self.assertEqual(item["claim"], "c4")
            self.assertIn("不充目标", item["reason"])

    def test_drill_sample_has_sku_level_evidence(self):
        sample = self.d["drill_sample"]
        self.assertTrue(sample)
        first = sample[0]
        self.assertIn("skus", first)
        self.assertIn("attributes", first["skus"][0])


if __name__ == "__main__":
    unittest.main()
