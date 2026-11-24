"""周结装配：覆盖实况、归功、缺口、快照争议与窗口建议。

输出同时给机器读（JSON）和人读（Markdown）。旧周结的事实截面通过其
known_through 重算保留；新周结把有争议的旧目标贡献逐笔挂回旧认领号。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .catalog import Catalog
from .coverage import (
    FILTERABLE, NO_SELLABLE, UNDETERMINED, BLANK, NEGATIVE,
    CoverageSummary, ExposureEvaluation, attribute_share,
    evaluate_all, filter_evaluations, page_view_key, summarize,
    topic_view_key,
)
from .model import DataBundle
from .planning import WindowPlanner
from .settlement import Settlement, diff_snapshots
from .timeutil import iso_week_label, parse_dt

STATUS_LABEL = {
    FILTERABLE: "可筛",
    NO_SELLABLE: "无可售规格",
    UNDETERMINED: "证据未判定",
    BLANK: "当周资料空白",
    NEGATIVE: "已判定无声明",
}


def _summary_block(summary: CoverageSummary) -> dict:
    return {
        "denominator": summary.denominator,
        "counts": {STATUS_LABEL[k]: v for k, v in summary.counts.items()},
        "shares": {STATUS_LABEL[k]: v for k, v in summary.shares.items()},
    }


def _topic_version_split(evaluations: list[ExposureEvaluation]) -> dict:
    """跨期专题定义变化：按版本拆开，标出可比范围。"""
    split: dict[str, dict] = {}
    for ev in evaluations:
        row = split.setdefault(ev.exposure.topic, {})
        key = f"v{ev.rule.version}({ev.rule.logic})"
        bucket = row.setdefault(key, {"events": 0, "units": set()})
        bucket["events"] += 1
        bucket["units"].add(topic_view_key(ev.exposure))
    result = {}
    for topic, versions in split.items():
        result[topic] = []
        for version, bucket in sorted(versions.items()):
            result[topic].append({
                "version": version,
                "events": bucket["events"],
                "deduped_units": len(bucket["units"]),
                "comparable": "仅与同逻辑版本可比",
            })
    return result


@dataclass
class WeeklyReport:
    as_of: str
    week: str
    data_dir: str
    previous_closes: list
    coverage_overall: dict
    coverage_by_attribute: dict
    coverage_by_topic: dict
    coverage_by_region: dict
    root_cause: dict
    attainment: dict
    team_ledgers: dict
    group_reduction: dict
    open_gaps: list
    in_transit: list
    snapshot_diff: dict
    topic_versions: dict
    family_continuity: list
    planning: dict
    drill_sample: list

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


def _jsonable(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in obj]
    return obj


def build_report(bundle: DataBundle, as_of, *, drill_limit: int = 12) -> WeeklyReport:
    as_of_dt = parse_dt(as_of) if isinstance(as_of, str) else as_of
    evaluations = evaluate_all(bundle, as_of_dt)
    # 当前快照只结算一次：快照对比会把争议标记挂回这些单元与台账。
    settlement = Settlement(bundle, evaluations, as_of_dt)
    diff = _previous_snapshot_diff(bundle, settlement, as_of_dt)

    overall = summarize(evaluations, page_view_key)
    by_attribute = {
        attr: _summary_block(
            attribute_share(
                filter_evaluations(evaluations, page=_page_for_attr(attr)),
                attr, page_view_key,
            )
        )
        for attr in ("未加蔗糖", "总糖已标")
    }
    by_topic = {}
    for topic in sorted({ev.exposure.topic for ev in evaluations}):
        subset = filter_evaluations(evaluations, topic=topic)
        by_topic[topic] = _summary_block(summarize(subset, topic_view_key))
    by_region = {}
    for region in sorted({ev.exposure.region for ev in evaluations}):
        subset = filter_evaluations(evaluations, region=region)
        by_region[region] = _summary_block(summarize(subset, page_view_key))

    root_cause = _root_causes(evaluations)
    attainment = settlement.topic_attainment()
    ledgers = {
        team: {
            "sole_units": len(ledger.sole_units),
            "joint_units": len(ledger.joint_units),
            "disputed_units": len(ledger.disputed_units),
            "in_transit_claims": sorted(ledger.in_transit_claims),
            "by_topic": ledger.by_topic,
        }
        for team, ledger in sorted(settlement.ledgers.items())
    }
    group = settlement.group_reduction()
    open_gaps = [_gap_row(settlement, unit) for unit in settlement.open_gaps()]
    in_transit = _in_transit_rows(bundle, settlement)
    families = _family_continuity(bundle)
    planner = WindowPlanner(bundle, settlement)
    planning = _planning_block(planner)
    drill = [ev.drill() for ev in evaluations[:drill_limit]]

    return WeeklyReport(
        as_of=as_of_dt.isoformat(),
        week=iso_week_label(as_of_dt),
        data_dir=str(bundle.source_dir),
        previous_closes=[
            {
                "week": c.week,
                "known_through": c.known_through.isoformat(),
                "issued_at": c.issued_at.isoformat(),
                "recipient": c.recipient,
            }
            for c in bundle.closes
        ],
        coverage_overall=_summary_block(overall),
        coverage_by_attribute=by_attribute,
        coverage_by_topic=by_topic,
        coverage_by_region=by_region,
        root_cause=root_cause,
        attainment=attainment,
        team_ledgers=ledgers,
        group_reduction=group,
        open_gaps=open_gaps,
        in_transit=in_transit,
        snapshot_diff=diff,
        topic_versions=_topic_version_split(evaluations),
        family_continuity=families,
        planning=planning,
        drill_sample=drill,
    )


def _page_for_attr(attribute: str) -> str | None:
    # 两个糖声明在现有专题中均出现于 p1；不过滤页面，保持全量口径。
    return None


def _root_causes(evaluations: list[ExposureEvaluation]) -> dict:
    """找不到商品的两层原因：无可售规格 vs 可售但无法按声明筛出。"""
    summary = summarize(evaluations, page_view_key)
    counts = summary.counts
    total = summary.denominator
    no_sellable = counts[NO_SELLABLE]
    sellable_not_filterable = (
        counts[UNDETERMINED] + counts[BLANK] + counts[NEGATIVE]
    )
    return {
        "deduped_page_units": total,
        "可筛": counts[FILTERABLE],
        "无可售规格": no_sellable,
        "可售但不可筛": sellable_not_filterable,
        "可售但不可筛明细": {
            STATUS_LABEL[UNDETERMINED]: counts[UNDETERMINED],
            STATUS_LABEL[BLANK]: counts[BLANK],
            STATUS_LABEL[NEGATIVE]: counts[NEGATIVE],
        },
    }


def _gap_row(settlement: Settlement, unit) -> dict:
    events = [
        ev for ev in settlement.evaluations
        if topic_view_key(ev.exposure) == unit.key
    ]
    return {
        "unit": list(unit.key),
        "topic": unit.topic,
        "region": unit.region,
        "page": unit.page,
        "status": STATUS_LABEL[unit.status],
        "events": unit.events,
        "in_transit_claims": unit.in_transit_claims,
        "note": unit.note,
        "skus": sorted({
            se.window.sku for ev in events for se in ev.sku_evaluations
        }),
        "attributes": sorted({
            attr for ev in events
            for se in ev.sku_evaluations
            for attr, res in se.by_attribute.items()
            if not res.is_support
        }),
    }


def _in_transit_rows(bundle: DataBundle, settlement: Settlement) -> list:
    rows = []
    for claim in bundle.accepted:
        if claim.is_confirmed:
            continue
        rows.append({
            "claim": claim.claim,
            "team": claim.team,
            "sku": claim.sku,
            "region": claim.region,
            "attribute": claim.attribute,
            "topic": claim.topic,
            "accepted_at": claim.accepted_at.isoformat(),
            "reason": "资料已登记但搜索页尚未确认更新，保留在途，不充目标",
        })
    return rows


def _previous_snapshot_diff(bundle: DataBundle,
                            current: Settlement, as_of_dt) -> dict:
    if not bundle.closes:
        return {"reopened": [], "reallocated": [], "self_healed": []}
    # 同一周结时点可能发给多个接收方，事实截面只算一次。
    cutoff = min(c.known_through for c in bundle.closes)
    old_evaluations = evaluate_all(bundle, cutoff)
    old_settlement = Settlement(bundle, old_evaluations, cutoff)
    diff = diff_snapshots(old_settlement, current)
    diff["old_snapshot"] = {
        "known_through": cutoff.isoformat(),
        "week": sorted({c.week for c in bundle.closes}),
        "note": "更正前周结保留原事实截面，仅用于追溯，不被覆盖",
    }
    return diff


def _family_continuity(bundle: DataBundle) -> list:
    catalog = Catalog(bundle)
    rows = []
    for family in sorted({w.family for w in bundle.availability}):
        rows.append({
            "family": family,
            "timeline": [
                {
                    "sku": w.sku,
                    "region": w.region,
                    "window": [w.begin.isoformat(), w.end.isoformat()],
                    "map_version": w.map_version,
                    "page": w.page,
                }
                for w in catalog.family_timeline(family)
            ],
            "note": "同配方换码可跨周对照原周口径，规格级事实不自动继承",
        })
    return rows


def _planning_block(planner: WindowPlanner) -> dict:
    recommendation = planner.recommend()

    def plan_dict(plan):
        if plan is None:
            return None
        return {
            "mode": plan.mode,
            "expected_reduction": plan.expected_reduction,
            "coverage_after": plan.coverage_after,
            "meets_standard": plan.meets_standard,
            "blocked_on": plan.blocked_on,
            "assignments": [
                {
                    "window": a.window.window,
                    "region": a.window.region,
                    "opens": a.window.opens.isoformat(),
                    "closes": a.window.closes.isoformat(),
                    "team": a.team,
                    "work": (
                        list(a.work.work_id) if a.work is not None else None
                    ),
                    "reason": a.reason,
                }
                for a in plan.assignments
            ],
        }

    return {
        "gap_works": [
            {
                "work": list(w.work_id),
                "page": w.page,
                "topics": w.topics,
                "candidate_teams": w.candidate_teams,
                "governable": w.governable,
                "block_reason": w.block_reason,
                "in_transit": w.in_transit,
                "existing_claim": w.existing_claim,
                "blocking_units": [list(u) for u in w.blocking_units],
            }
            for w in planner.works
        ],
        "baseline": plan_dict(recommendation["baseline"]),
        "best_reassign": plan_dict(recommendation["best_reassign"]),
        "best_joint": plan_dict(recommendation["best_joint"]),
        "shortages": recommendation["shortages"],
    }
