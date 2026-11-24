"""可售规格、同配方族与专题规则版本查询。"""
from __future__ import annotations

from .model import (
    DataBundle, SellableWindow, TopicRule, LOGIC_UNION, LOGIC_INTERSECT,
)


class Catalog:
    def __init__(self, bundle: DataBundle):
        self.bundle = bundle
        self._windows: dict[tuple[str, str], list[SellableWindow]] = {}
        for win in bundle.availability:
            self._windows.setdefault((win.page, win.region), []).append(win)
        for entries in self._windows.values():
            entries.sort(key=lambda w: (w.begin, w.end))
        self._topic_index: dict[tuple[str, str], list[TopicRule]] = {}
        for rule in bundle.topics:
            self._topic_index.setdefault((rule.topic, rule.page), []).append(rule)
        for entries in self._topic_index.values():
            entries.sort(key=lambda t: t.change_at)

    def sellable_skus(self, page: str, region: str, at) -> list[SellableWindow]:
        """当时该页面在该地区处于可售窗内的规格（含映射版本）。"""
        return [
            w for w in self._windows.get((page, region), ())
            if w.covers(at)
        ]

    def family_timeline(self, family: str) -> list[SellableWindow]:
        """同配方跨规格、跨地区、跨周的窗口，用于换码对照原周口径。"""
        return sorted(
            (w for w in self.bundle.availability if w.family == family),
            key=lambda w: (w.region, w.begin, w.sku),
        )

    def rule_at(self, topic: str, page: str, at) -> TopicRule:
        """取曝光当时生效的专题版本；change_at 之前沿用旧规则。"""
        versions = self._topic_index[(topic, page)]
        current = versions[0]
        for rule in versions:
            if rule.change_at <= at:
                current = rule
            else:
                break
        return current

    def rules_on(self, topic: str, page: str) -> list[TopicRule]:
        return list(self._topic_index.get((topic, page), ()))

    @staticmethod
    def sku_matches(per_attr: dict[str, bool], logic: str) -> bool:
        """同一规格上按当时界面配置判断多条件，并为交集、或为并集。"""
        if logic == LOGIC_UNION:
            return any(per_attr.values())
        if logic == LOGIC_INTERSECT:
            return all(per_attr.values())
        raise ValueError(f"未知筛选逻辑: {logic}")
