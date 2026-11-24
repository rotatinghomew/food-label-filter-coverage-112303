"""声明事实的时点解析。

同一 (sku, region, attribute) 可有多条事实：只有 ``known`` 不晚于资料截止
（as_of）的条目才登记在案；已登记条目中取 ``known`` 最新者，再要求其
``start`` 已生效。空值（无任何已登记条目）不等于“否”，单列资料空白。
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import ClaimFact, STATE_UNKNOWN


@dataclass(frozen=True)
class FactResolution:
    state: str | None          # None 表示资料空白（无登记）
    fact: ClaimFact | None     # 命中的最新登记条目
    registered: bool           # 截止时点是否已有任何登记
    effective: bool            # 最新条目在查询时点是否已生效

    @property
    def is_support(self) -> bool:
        return self.effective and self.state == "支持"


class FactResolver:
    def __init__(self, claims: list[ClaimFact]):
        self._index: dict[tuple[str, str, str], list[ClaimFact]] = {}
        for fact in claims:
            key = (fact.sku, fact.region, fact.attribute)
            self._index.setdefault(key, []).append(fact)
        for entries in self._index.values():
            entries.sort(key=lambda f: (f.known, f.start, f.fact))

    def resolve(self, sku: str, region: str, attribute: str,
                at, as_of) -> FactResolution:
        entries = [
            f for f in self._index.get((sku, region, attribute), ())
            if f.known <= as_of
        ]
        if not entries:
            return FactResolution(None, None, False, False)
        latest = entries[-1]
        effective = latest.start <= at
        return FactResolution(latest.state, latest, True, effective)

    def latest_registered(self, sku: str, region: str,
                          attribute: str, as_of) -> ClaimFact | None:
        entries = [
            f for f in self._index.get((sku, region, attribute), ())
            if f.known <= as_of
        ]
        return entries[-1] if entries else None

    def known_team(self, sku: str, region: str,
                   attribute: str, as_of) -> str:
        """登记条目的责任团队，未知态也可能带团队（如撤回方）。"""
        latest = self.latest_registered(sku, region, attribute, as_of)
        return latest.team if latest else ""


def support_withdrawn_to_unknown(previous: FactResolution,
                                 current: FactResolution) -> bool:
    """归属更正识别：先前支持、当前变为证据未判定。"""
    prev_support = previous.is_support
    cur_unknown = (
        current.registered and current.effective and current.state == STATE_UNKNOWN
    )
    return prev_support and cur_unknown
