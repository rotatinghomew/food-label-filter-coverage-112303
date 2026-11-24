"""新一轮治理的改版窗口试算与建议分配。

接受的建议只能落在 windows.csv 给出的真实窗口。已接受计划的窗口是既有
窗口承诺，试算必须保留；same_team 相同的窗口在重合时段互斥。一个地区
窗口不够时同时给出合做与改派两套试算，并对照合同各区最低覆盖；找不到
同时达标的安排时指出缺的是哪项共用事实还是哪个窗口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from .coverage import topic_view_key
from .model import AcceptedClaim, ChangeWindow, DataBundle
from .settlement import Settlement


@dataclass
class GapWork:
    """一个可治理缺口：共用规格事实可能同时服务多个专题。"""
    sku: str
    region: str
    attribute: str
    page: str
    topics: list[str]
    blocking_units: list[tuple] = field(default_factory=list)
    candidate_teams: list[str] = field(default_factory=list)
    existing_claim: str = ""
    in_transit: bool = False
    governable: bool = True
    block_reason: str = ""

    @property
    def work_id(self) -> tuple[str, str, str]:
        return self.sku, self.region, self.attribute


def discover_gap_work(settlement: Settlement) -> list[GapWork]:
    """从仍开放的缺口与在途认领汇总治理工作项。

    同一 (sku, region, attribute) 被多个专题阻塞时合并为一个共用事实工作，
    便于呈现“改一个共用规格可补两个专题”与“只让一个专题可筛”的差别。
    已判定无声明是否定事实，资料登记无法翻正，标记为不可治理；资料空白
    与证据未判定才是可派活的共用事实缺口。
    """
    works: dict[tuple, GapWork] = {}
    for unit in settlement.open_gaps():
        for ev in settlement.evaluations:
            if topic_view_key(ev.exposure) != unit.key:
                continue
            for se in ev.sku_evaluations:
                for attr, resolution in se.by_attribute.items():
                    if resolution.is_support:
                        continue
                    wid = (se.window.sku, ev.exposure.region, attr)
                    work = works.get(wid)
                    if work is None:
                        work = GapWork(
                            sku=se.window.sku, region=ev.exposure.region,
                            attribute=attr, page=ev.exposure.page,
                            topics=[],
                        )
                        works[wid] = work
                    if unit.topic not in work.topics:
                        work.topics.append(unit.topic)
                    if unit.key not in work.blocking_units:
                        work.blocking_units.append(unit.key)
                    # 否定事实一旦出现，该工作项不能靠资料治理翻正。
                    if resolution.registered and resolution.effective \
                            and resolution.fact is not None \
                            and resolution.fact.state == "无声明":
                        work.governable = False
                        work.block_reason = "已判定无声明，需商品侧而非资料侧治理"
                    elif not work.governable:
                        pass
                    elif not resolution.registered:
                        work.block_reason = "当周资料空白，尚无任何登记"
                    else:
                        work.block_reason = "证据未判定，需补支持证据或更正"
                    for claim in settlement._claims_for(
                        se.window.sku, ev.exposure.region, attr
                    ):
                        if claim.topic == unit.topic and claim.team:
                            if claim.team not in work.candidate_teams:
                                work.candidate_teams.append(claim.team)
                            if not claim.is_confirmed:
                                work.in_transit = True
                                work.existing_claim = claim.claim
    return sorted(works.values(), key=lambda w: w.work_id)


def _overlap(left: ChangeWindow, right: ChangeWindow) -> bool:
    return left.opens < right.closes and right.opens < left.closes


def _page_key(unit) -> tuple:
    """从专题单元键取页面去重键（split, session, page, region）。"""
    return unit.key[0], unit.key[1], unit.key[2], unit.region


@dataclass
class Assignment:
    window: ChangeWindow
    work: GapWork | None          # None 表示窗口闲置/保留给既有承诺
    team: str
    reason: str = ""


@dataclass
class AllocationPlan:
    mode: str                     # 保留承诺 / 改派 / 合做
    assignments: list[Assignment]
    expected_reduction: dict[str, int]   # region -> 页面去重缺口减少数
    coverage_after: dict[str, float]
    meets_standard: dict[str, bool]
    blocked_on: list[str]

    @property
    def total_reduction(self) -> int:
        return sum(self.expected_reduction.values())

    @property
    def all_standards_met(self) -> bool:
        return all(self.meets_standard.values())


class WindowPlanner:
    def __init__(self, bundle: DataBundle, settlement: Settlement):
        self.bundle = bundle
        self.settlement = settlement
        self.works = discover_gap_work(settlement)
        self.windows = list(bundle.windows)
        self.standards = {s.region: s for s in bundle.standards}
        self._validate_commitments()

    def _validate_commitments(self):
        committed = [w for w in self.windows if w.accepted_claim]
        for win in committed:
            claim = self._claim(win.accepted_claim)
            if claim is None:
                raise ValueError(f"窗口 {win.window} 承诺的认领不存在")
            if win.region != claim.region:
                raise ValueError(f"窗口 {win.window} 地区与承诺认领不一致")

    def _claim(self, claim_id: str) -> AcceptedClaim | None:
        for claim in self.bundle.accepted:
            if claim.claim == claim_id:
                return claim
        return None

    def _team_busy(self, team: str, window: ChangeWindow,
                   picked: list[Assignment]) -> bool:
        for assignment in picked:
            other = assignment.window
            if (other.same_team == team and window.same_team == team
                    and _overlap(other, window)):
                return True
        return False

    def _eligible_teams(self, window: ChangeWindow,
                        work: GapWork) -> list[str]:
        if window.region != work.region:
            return []
        if work.candidate_teams:
            # 有候选团队时只派给事实责任团队，避免把窗口班组当成万能团队。
            return list(work.candidate_teams)
        # 无认领的新工作：窗口所属班组可接单，合做模式再允许其他班组。
        return [window.same_team]

    def baseline_page_units(self, region: str) -> int:
        keys = {
            _page_key(unit) for unit in self.settlement.units.values()
            if unit.region == region
        }
        return len(keys)

    def baseline_reduced(self, region: str) -> int:
        keys = set()
        for unit in self.settlement.units.values():
            if unit.region == region and unit.credits and not unit.is_gap:
                keys.add(_page_key(unit))
        return len(keys)

    def _unit_requirements(self) -> dict[tuple, set[tuple]]:
        """每个开放专题单元的补齐条件。

        交集：所有缺失的共用事实工作都完成才算补上（AND）。
        并集：任一缺失工作完成即可补上，故每个工作单独构成一条可行条件，
        模拟时只要命中其中一条就算该单元被减少。
        含不可治理否定事实的单元不在可补齐集合内。
        """
        required: dict[tuple, set[tuple]] = {}
        for unit in self.settlement.units.values():
            if not unit.is_gap:
                continue
            logic = next(
                ev.rule.logic for ev in self.settlement.evaluations
                if topic_view_key(ev.exposure) == unit.key
            )
            missing: set[tuple] = set()
            has_ungovernable = False
            for work in self.works:
                if unit.key not in work.blocking_units:
                    continue
                if not work.governable:
                    has_ungovernable = True
                else:
                    missing.add(work.work_id)
            if has_ungovernable or not missing:
                continue
            # 交/并的差别在 _simulate_reduction 按单元逻辑解释，
            # 这里统一登记“该单元缺的可治理工作集合”。
            required[unit.key] = set(missing)
        return required

    def _simulate_reduction(self, assignments: list[Assignment]):
        """按假设事实补齐模拟联合可减少的页面去重缺口。

        交集单元要求全部阻塞工作完成；并集单元完成任一阻塞工作即补上。
        页面口径按 (split, session, page) 去重。
        """
        completed: set[tuple[str, str, str]] = {
            a.work.work_id for a in assignments if a.work is not None
        }
        requirements = self._unit_requirements()
        union_units = self._union_unit_keys()
        helped_units: dict[str, set] = {}
        for unit_key, need in requirements.items():
            satisfied = (
                bool(need & completed) if unit_key in union_units
                else need <= completed
            )
            if not satisfied:
                continue
            region = next(
                u.region for u in self.settlement.units.values()
                if u.key == unit_key
            )
            helped_units.setdefault(region, set()).add(
                _page_key(self.settlement.units[unit_key])
            )
        region_reduction: dict[str, int] = {}
        coverage_after: dict[str, float] = {}
        for region in sorted({w.region for w in self.windows}):
            base_total = self.baseline_page_units(region)
            base_reduced = self.baseline_reduced(region)
            reduction = len(helped_units.get(region, set()))
            region_reduction[region] = reduction
            coverage_after[region] = (
                round((base_reduced + reduction) / base_total, 4)
                if base_total else 0.0
            )
        return region_reduction, coverage_after

    def _union_unit_keys(self) -> set[tuple]:
        keys = set()
        for unit in self.settlement.units.values():
            if not unit.is_gap:
                continue
            logic = next(
                ev.rule.logic for ev in self.settlement.evaluations
                if topic_view_key(ev.exposure) == unit.key
            )
            if logic == "并":
                keys.add(unit.key)
        return keys

    def _standards(self, coverage_after: dict[str, float]):
        meets = {}
        blocked = []
        for region, standard in self.standards.items():
            ok = coverage_after.get(region, 0.0) >= standard.min_coverage
            meets[region] = ok
            if not ok:
                gap = round(standard.min_coverage - coverage_after[region], 4)
                blocked.append(
                    f"{region}覆盖率 {coverage_after.get(region, 0.0):.4f} "
                    f"低于合同 {standard.min_coverage:.2f}，缺口 {gap:.4f}"
                )
        return meets, blocked

    def enumerate_plans(self, *, joint: bool) -> list[AllocationPlan]:
        """枚举可行分配。joint=False 只允许改派，True 允许合做（多队共窗）。"""
        free_windows = [w for w in self.windows if not w.accepted_claim]
        committed = [
            Assignment(w, None, w.same_team,
                       reason=f"保留既有承诺 {w.accepted_claim}")
            for w in self.windows if w.accepted_claim
        ]
        options_per_window: list[list[Assignment | None]] = []
        governable_works = [w for w in self.works if w.governable]
        for window in free_windows:
            choices: list[Assignment | None] = [None]  # 闲置
            for work in governable_works:
                if window.region != work.region:
                    continue
                for team in self._eligible_teams(window, work):
                    reason = ""
                    if team != window.same_team:
                        reason = (
                            f"跨班组合做：窗口班组 {window.same_team}，"
                            f"由事实责任团队 {team} 执行"
                        )
                    choices.append(Assignment(window, work, team, reason))
            options_per_window.append(choices)

        plans: list[AllocationPlan] = []
        for combo in product(*options_per_window):
            picked = list(committed)
            feasible = True
            used_work: set[tuple] = set()
            for assignment in combo:
                if assignment is None:
                    continue
                if assignment.work.work_id in used_work:
                    feasible = False
                    break
                if self._team_busy(assignment.team, assignment.window, picked):
                    feasible = False
                    break
                if not joint and assignment.team != assignment.window.same_team:
                    feasible = False
                    break
                picked.append(assignment)
                used_work.add(assignment.work.work_id)
            if not feasible:
                continue
            reduction, coverage = self._simulate_reduction(picked)
            meets, blocked = self._standards(coverage)
            mode = "合做" if joint else "改派"
            plans.append(
                AllocationPlan(mode, picked, reduction, coverage,
                               meets, blocked)
            )
        plans.sort(
            key=lambda p: (
                -p.total_reduction,
                sum(1 for ok in p.meets_standard.values() if not ok),
            ),
        )
        return plans

    def recommend(self) -> dict:
        """返回承诺基线、改派最优与合做最优，并解释不达标时缺什么。"""
        committed = [
            Assignment(w, None, w.same_team,
                       reason=f"保留既有承诺 {w.accepted_claim}")
            for w in self.windows if w.accepted_claim
        ]
        base_reduction, base_coverage = self._simulate_reduction(committed)
        base_meets, base_blocked = self._standards(base_coverage)
        reassign = self.enumerate_plans(joint=False)
        joint_plans = self.enumerate_plans(joint=True)
        result = {
            "baseline": AllocationPlan(
                "保留承诺", committed, base_reduction, base_coverage,
                base_meets, base_blocked,
            ),
            "best_reassign": reassign[0] if reassign else None,
            "best_joint": joint_plans[0] if joint_plans else None,
            "shortages": self._shortages(
                joint_plans[0] if joint_plans else None
            ),
        }
        return result

    def _shortages(self, best: AllocationPlan | None) -> list[str]:
        """区分缺的是窗口还是共用事实。"""
        notes = []
        if best is None:
            notes.append("没有可落地的窗口分配，所有免费窗口均无法匹配治理工作")
            return notes
        for region, ok in best.meets_standard.items():
            if ok:
                continue
            region_windows = [w for w in self.windows if w.region == region]
            free = [w for w in region_windows if not w.accepted_claim]
            region_works = [w for w in self.works
                            if w.region == region and w.governable]
            blocked_works = [w for w in self.works
                             if w.region == region and not w.governable]
            if len(free) < len(region_works):
                notes.append(
                    f"{region}缺窗口：{len(region_works)} 项共用事实工作，"
                    f"仅 {len(free)} 个未承诺窗口（"
                    f"{', '.join(w.window for w in free) or '无'}）"
                )
            else:
                notes.append(
                    f"{region}缺共用事实："
                    + ("、".join(
                        f"{w.sku}/{w.attribute}" for w in region_works
                    ) or "无待治理工作")
                )
            for work in blocked_works:
                notes.append(
                    f"{region} {work.sku}/{work.attribute} 不可由资料治理解决："
                    f"{work.block_reason}"
                )
        return notes
