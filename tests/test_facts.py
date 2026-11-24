"""时间与声明事实的时点语义测试。"""
import unittest

from weekly.facts import FactResolver
from weekly.model import ClaimFact, STATE_SUPPORT, STATE_UNKNOWN, STATE_NONE
from weekly.timeutil import parse_dt, iso_week_label


def fact(fact, sku, region, attr, state, start, known, team=""):
    return ClaimFact(fact, sku, region, attr, state,
                     parse_dt(start), parse_dt(known), team)


class FactResolverTest(unittest.TestCase):
    def setUp(self):
        self.resolver = FactResolver([
            fact("f3", "a2", "东", "未加蔗糖", STATE_SUPPORT,
                 "2026-08-29T10:00", "2026-08-29T08:00", "甲"),
            # 归属更正：09-04 登记未知，撤回 f3 的支持。
            fact("f6", "a2", "东", "未加蔗糖", STATE_UNKNOWN,
                 "2026-08-29T10:00", "2026-09-04T08:00", "甲"),
            fact("f7", "b1", "东", "总糖已标", STATE_NONE,
                 "2026-08-24T00:00", "2026-08-24T00:00"),
        ])

    def test_blank_is_not_negative(self):
        res = self.resolver.resolve("x9", "东", "未加蔗糖",
                                    parse_dt("2026-08-30T00:00"),
                                    parse_dt("2026-09-07T00:00"))
        self.assertFalse(res.registered)
        self.assertIsNone(res.state)
        self.assertFalse(res.is_support)

    def test_as_of_ignores_later_registration(self):
        # 旧周结截止 08-31 13:00，f6 尚未登记，f3 仍支持。
        old = self.resolver.resolve("a2", "东", "未加蔗糖",
                                    parse_dt("2026-08-30T00:00"),
                                    parse_dt("2026-08-31T13:00"))
        self.assertTrue(old.is_support)
        self.assertEqual(old.fact.fact, "f3")
        # 新截面 f6 已登记，最新条目变为未知。
        new = self.resolver.resolve("a2", "东", "未加蔗糖",
                                    parse_dt("2026-08-30T00:00"),
                                    parse_dt("2026-09-07T05:00"))
        self.assertEqual(new.state, STATE_UNKNOWN)
        self.assertFalse(new.is_support)

    def test_start_not_yet_effective(self):
        resolver = FactResolver([
            fact("fx", "z", "东", "总糖已标", STATE_SUPPORT,
                 "2026-09-01T10:00", "2026-08-30T08:00", "乙"),
        ])
        res = resolver.resolve("z", "东", "总糖已标",
                               parse_dt("2026-08-31T00:00"),
                               parse_dt("2026-09-07T00:00"))
        self.assertTrue(res.registered)
        self.assertFalse(res.effective)
        self.assertFalse(res.is_support)

    def test_negative_evidence_is_determined(self):
        res = self.resolver.resolve("b1", "东", "总糖已标",
                                    parse_dt("2026-08-30T00:00"),
                                    parse_dt("2026-09-07T00:00"))
        self.assertEqual(res.state, STATE_NONE)
        self.assertFalse(res.is_support)

    def test_iso_week(self):
        # 2026-08-31 是周一（W36 起），2026-09-07 是再下一个周一（W37）。
        self.assertEqual(iso_week_label(parse_dt("2026-08-30T13:00")),
                         "2026-W35")
        self.assertEqual(iso_week_label(parse_dt("2026-08-31T13:00")),
                         "2026-W36")
        self.assertEqual(iso_week_label(parse_dt("2026-09-07T05:00")),
                         "2026-W37")


if __name__ == "__main__":
    unittest.main()
