"""曝光覆盖判定与多维聚合。

判定顺序：先看当时当地是否有可售规格；再对每个可售规格按专题当时版本的
并/交逻辑，在同一规格上判断所选条件。筛选日志不记录包装，故只认规格级
事实。非可筛事件按“证据未判定 > 资料空白 > 已判定无声明”的阻塞优先级
归桶：未判定可能翻正，优先级最高；空白是从未登记；无声明是否定事实。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog
from .facts import FactResolution, FactResolver
from .model import (
    DataBundle, Exposure, SellableWindow, TopicRule,
    STATE_NONE, STATE_UNKNOWN,
)

FILTERABLE = "可筛"
NO_SELLABLE = "无可售规格"
UNDETERMINED = "证据未判定"
BLANK = "资料空白"
NEGATIVE = "已判定无声明"

# 非可筛归桶优先级（数字越大阻塞性越强、越先报）。
_FAILURE_RANK = {UNDETERMINED: 3, BLANK: 2, NEGATIVE: 1}
ALL_STATUSES = (FILTERABLE, NO_SELLABLE, UNDETERMINED, BLANK, NEGATIVE)


@dataclass
class SkuEvaluation:
    window: SellableWindow
    by_attribute: dict[str, FactResolution]
    logic: str
    per_attribute_support: dict[str, bool]

    @property
    def matches(self) -> bool:
        if self.logic == "并":
            return any(self.per_attribute_support.values())
        return all(self.per_attribute_support.values())

    @property
    def failure_status(self) -> str:
        statuses = []
        for attr, resolution in self.by_attribute.items():
            if self.per_attribute_support[attr]:
                continue
            if not resolution.registered:
                statuses.append(BLANK)
            elif not resolution.effective:
                # 已登记但未生效：生效前视同资料未到位，归未判定。
                statuses.append(UNDETERMINED)
            elif resolution.state == STATE_UNKNOWN:
                statuses.append(UNDETERMINED)
            elif resolution.state == STATE_NONE:
                statuses.append(NEGATIVE)
            else:  # pragma: no cover - 防御分支
                statuses.append(UNDETERMINED)
        if not statuses:
            return FILTERABLE
        return max(statuses, key=lambda s: _FAILURE_RANK[s])


@dataclass
class ExposureEvaluation:
    exposure: Exposure
    rule: TopicRule
    as_of: object
    sku_evaluations: list[SkuEvaluation]
    status: str
    matched: list[SkuEvaluation] = field(default_factory=list)

    @property
    def filterable(self) -> bool:
        return self.status == FILTERABLE

    def attribute_supported(self, attribute: str) -> bool:
        """该曝光下是否有任意可售规格支持某一声明（单条件口径）。"""
        for sku_eval in self.sku_evaluations:
            if sku_eval.per_attribute_support.get(attribute):
                return True
        return False

    def resolutions_of(self, sku: str) -> dict[str, FactResolution]:
        for sku_eval in self.sku_evaluations:
            if sku_eval.window.sku == sku:
                return sku_eval.by_attribute
        return {}

    def drill(self) -> dict:
        """下钻视图：可用规格、窗口与每个条件的事实状态。"""
        return {
            "event": self.exposure.event,
            "topic": self.exposure.topic,
            "version": self.rule.version,
            "logic": self.rule.logic,
            "page": self.exposure.page,
            "region": self.exposure.region,
            "seen_at": self.exposure.seen_at.isoformat(),
            "conditions": list(self.exposure.conditions),
            "status": self.status,
            "skus": [
                {
                    "sku": se.window.sku,
                    "family": se.window.family,
                    "map_version": se.window.map_version,
                    "window": [
                        se.window.begin.isoformat(),
                        se.window.end.isoformat(),
                    ],
                    "attributes": {
                        attr: {
                            "state": res.state,
                            "registered": res.registered,
                            "effective": res.effective,
                            "fact": res.fact.fact if res.fact else None,
                            "team": res.fact.team if res.fact else "",
                        }
                        for attr, res in se.by_attribute.items()
                    },
                    "matches": se.matches,
                }
                for se in self.sku_evaluations
            ],
        }


def evaluate_exposure(exposure: Exposure, catalog: Catalog,
                      resolver: FactResolver, as_of) -> ExposureEvaluation:
    rule = catalog.rule_at(exposure.topic, exposure.page, exposure.seen_at)
    # 日志记录当时实际选中的条件，允许是专题配置的子集；超出配置则报错。
    extra = set(exposure.conditions) - set(rule.attributes)
    if extra:
        raise ValueError(
            f"{exposure.event} 所选条件 {exposure.conditions} 超出专题版本 "
            f"{rule.topic} v{rule.version} 配置 {rule.attributes}"
        )
    if not exposure.conditions:
        raise ValueError(f"{exposure.event} 没有任何已选条件")
    windows = catalog.sellable_skus(exposure.page, exposure.region,
                                    exposure.seen_at)
    sku_evaluations: list[SkuEvaluation] = []
    for window in windows:
        by_attr: dict[str, FactResolution] = {}
        support: dict[str, bool] = {}
        for attr in exposure.conditions:
            resolution = resolver.resolve(window.sku, exposure.region,
                                          attr, exposure.seen_at, as_of)
            by_attr[attr] = resolution
            support[attr] = resolution.is_support
        sku_evaluations.append(
            SkuEvaluation(window, by_attr, rule.logic, support)
        )

    matched = [se for se in sku_evaluations if se.matches]
    if matched:
        status = FILTERABLE
    elif not sku_evaluations:
        status = NO_SELLABLE
    else:
        status = max(
            (se.failure_status for se in sku_evaluations),
            key=lambda s: _FAILURE_RANK[s],
        )
    return ExposureEvaluation(exposure, rule, as_of,
                              sku_evaluations, status, matched)


def evaluate_all(bundle: DataBundle, as_of) -> list[ExposureEvaluation]:
    catalog = Catalog(bundle)
    resolver = FactResolver(bundle.claims)
    return [
        evaluate_exposure(exp, catalog, resolver, as_of)
        for exp in bundle.exposures
    ]


def _merge_status(left: str, right: str) -> str:
    if left == FILTERABLE or right == FILTERABLE:
        return FILTERABLE
    if left == NO_SELLABLE and right == NO_SELLABLE:
        return NO_SELLABLE
    candidates = [s for s in (left, right) if s != NO_SELLABLE]
    if not candidates:  # pragma: no cover - 上面已覆盖
        return NO_SELLABLE
    return max(candidates, key=lambda s: _FAILURE_RANK.get(s, 0))


@dataclass
class CoverageSummary:
    """同一去重口径下的状态计数与份额。"""
    counts: dict[str, int]
    denominator: int

    @property
    def shares(self) -> dict[str, float]:
        if self.denominator == 0:
            return {s: 0.0 for s in ALL_STATUSES}
        return {
            s: round(self.counts.get(s, 0) / self.denominator, 4)
            for s in ALL_STATUSES
        }


def summarize(evaluations: list[ExposureEvaluation], key_fn) -> CoverageSummary:
    deduped: dict = {}
    for evaluation in evaluations:
        key = key_fn(evaluation.exposure)
        status = evaluation.status
        if key not in deduped:
            deduped[key] = status
        else:
            deduped[key] = _merge_status(deduped[key], status)
    counts = {status: 0 for status in ALL_STATUSES}
    for status in deduped.values():
        counts[status] += 1
    return CoverageSummary(counts, len(deduped))


def attribute_share(evaluations: list[ExposureEvaluation],
                    attribute: str, key_fn) -> CoverageSummary:
    """单声明口径：可把该声明筛出的页面份额（按同一去重键）。

    非可筛单元按阻塞优先级保留未判定/空白/无声明三态，避免把否定证据
    误算成资料空白。
    """
    deduped: dict = {}
    for evaluation in evaluations:
        key = key_fn(evaluation.exposure)
        if evaluation.attribute_supported(attribute):
            deduped[key] = FILTERABLE
            continue
        # 找出该声明在本单元各可售规格上的最差阻塞状态。
        worst = NO_SELLABLE
        for se in evaluation.sku_evaluations:
            resolution = se.by_attribute.get(attribute)
            if resolution is None:
                continue
            if not resolution.registered:
                candidate = BLANK
            elif not resolution.effective or resolution.state == STATE_UNKNOWN:
                candidate = UNDETERMINED
            elif resolution.state == STATE_NONE:
                candidate = NEGATIVE
            else:  # pragma: no cover - 防御
                candidate = UNDETERMINED
            worst = _merge_status(worst, candidate) if worst != NO_SELLABLE else candidate
        if key not in deduped:
            deduped[key] = worst
        else:
            deduped[key] = _merge_status(deduped[key], worst)
    counts = {status: 0 for status in ALL_STATUSES}
    for status in deduped.values():
        counts[status] += 1
    return CoverageSummary(counts, len(deduped))


def page_view_key(exposure: Exposure):
    """页面按一次货品展示：沿用日志 split 分流键的会话+页面。"""
    return exposure.page_key


def topic_view_key(exposure: Exposure):
    return exposure.topic_key


def filter_evaluations(evaluations: list[ExposureEvaluation], *,
                       topic: str | None = None, region: str | None = None,
                       page: str | None = None, sku: str | None = None,
                       family: str | None = None,
                       status: str | None = None) -> list[ExposureEvaluation]:
    """多维下钻过滤。"""
    result = []
    for evaluation in evaluations:
        exp = evaluation.exposure
        if topic is not None and exp.topic != topic:
            continue
        if region is not None and exp.region != region:
            continue
        if page is not None and exp.page != page:
            continue
        if status is not None and evaluation.status != status:
            continue
        if sku is not None and not any(
            se.window.sku == sku for se in evaluation.sku_evaluations
        ):
            continue
        if family is not None and not any(
            se.window.family == family for se in evaluation.sku_evaluations
        ):
            continue
        result.append(evaluation)
    return result
