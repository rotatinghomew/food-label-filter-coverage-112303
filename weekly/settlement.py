"""治理认领的结算、归功与跨周结争议重审。

结算单位沿用曝光的两套去重口径：专题达成度按 (split, session, page, topic)
去重；治理群组实际减少的不可筛曝光按 (split, session, page) 去重，跨专题
合计天然去重。一个认领只按其实际生效的地区与筛选条件计入；仅登记未确认
的保留在途，不充目标。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .coverage import FILTERABLE, topic_view_key
from .facts import FactResolver
from .model import AcceptedClaim, DataBundle

SOLE = "单独"
JOINT = "共同"
IN_TRANSIT = "在途"
DISPUTED = "争议重审"
SELF_HEALED = "规则自愈"


@dataclass
class GapUnit:
    """一个专题去重口径下的曝光单元及其结算结论。"""
    key: tuple
    topic: str
    region: str
    page: str
    status: str
    events: list[str]
    # 团队 -> (认领号, 贡献形态, 是否跨专题外溢)
    # 外溢：认领登记在别的专题，但其共用事实让本单元可筛，
    # 计入治理群组减少，不计入本专题目标达成度。
    credits: dict[str, tuple[str, str, bool]] = field(default_factory=dict)
    in_transit_claims: list[str] = field(default_factory=list)
    disputed_claims: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def is_gap(self) -> bool:
        return self.status != FILTERABLE

    @property
    def credited_teams(self) -> list[str]:
        return list(self.credits)

    @property
    def primary_credits(self) -> dict[str, tuple[str, str, bool]]:
        """计入本专题目标达成度的认领（非外溢）。"""
        return {t: v for t, v in self.credits.items() if not v[2]}


@dataclass
class TeamLedger:
    team: str
    sole_units: set = field(default_factory=set)
    joint_units: set = field(default_factory=set)
    disputed_units: set = field(default_factory=set)
    in_transit_claims: set = field(default_factory=set)
    by_topic: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def credit_units(self) -> set:
        return self.sole_units | self.joint_units


class Settlement:
    def __init__(self, bundle: DataBundle,
                 evaluations: list[ExposureEvaluation], as_of):
        self.bundle = bundle
        self.evaluations = evaluations
        self.as_of = as_of
        self.resolver = FactResolver(bundle.claims)
        self._claims_by_key: dict[tuple, list[AcceptedClaim]] = {}
        for claim in bundle.accepted:
            self._claims_by_key.setdefault(
                (claim.sku, claim.region, claim.attribute), []
            ).append(claim)
        self.units: dict[tuple, GapUnit] = {}
        self._build_units()
        self._settle()

    # ------------------------------------------------------------------
    def _build_units(self):
        for evaluation in self.evaluations:
            exp = evaluation.exposure
            key = topic_view_key(exp)
            unit = self.units.get(key)
            if unit is None:
                unit = GapUnit(
                    key=key, topic=exp.topic, region=exp.region,
                    page=exp.page, status=evaluation.status,
                    events=[exp.event],
                )
                self.units[key] = unit
            else:
                unit.events.append(exp.event)
                from .coverage import _merge_status
                unit.status = _merge_status(unit.status, evaluation.status)

    def _claims_for(self, sku: str, region: str, attribute: str):
        return self._claims_by_key.get((sku, region, attribute), [])

    def _settle(self):
        """对当前快照结算：找出让每个单元可筛的确认认领并归功。"""
        for unit in self.units.values():
            if unit.status != FILTERABLE:
                self._mark_in_transit(unit)
                continue
            self._credit_filterable(unit)
        self.ledgers = self._build_ledgers()

    def _matching_evaluations(self, unit: GapUnit):
        return [
            ev for ev in self.evaluations
            if topic_view_key(ev.exposure) == unit.key
        ]

    def _mark_in_transit(self, unit: GapUnit):
        """缺口单元上已登记但未确认（或事实未到位）的认领保留在途。"""
        for ev in self._matching_evaluations(unit):
            for se in ev.sku_evaluations:
                for attr in ev.exposure.conditions:
                    for claim in self._claims_for(
                        se.window.sku, ev.exposure.region, attr
                    ):
                        if claim.region != ev.exposure.region:
                            continue
                        if not claim.is_confirmed:
                            unit.in_transit_claims.append(claim.claim)
        unit.in_transit_claims = sorted(set(unit.in_transit_claims))

    def _credit_filterable(self, unit: GapUnit):
        """并集：任一确认认领单独补上即单独归功，多团队同刻则共同。

        交集：所需条件分别由不同团队满足时整笔算共同贡献；同一团队补齐
        全部条件算单独。确认时间用于重叠治理的先后归功。认领按事实键
        （sku/region/attribute）匹配而不限认领登记专题：共用规格的一次
        资料治理可外溢到其他专题，外溢单独标记，不充该专题目标。
        """
        logic = self._matching_evaluations(unit)[0].rule.logic
        # 团队 -> 该团队在本单元确认有效的 (认领, 确认时刻, 是否外溢)
        team_hits: dict[str, list[tuple[AcceptedClaim, object, bool]]] = {}
        seen_claims: set[str] = set()
        for ev in self._matching_evaluations(unit):
            for se in ev.sku_evaluations:
                if not se.matches:
                    continue
                per_attr_claim: dict[str, AcceptedClaim] = {}
                for attr, supported in se.per_attribute_support.items():
                    if not supported:
                        continue
                    for claim in self._claims_for(
                        se.window.sku, ev.exposure.region, attr
                    ):
                        # 认领只按其实际生效的地区与筛选条件计入，
                        # 跨地区同规格也不得借用。
                        if claim.region != ev.exposure.region:
                            continue
                        if not claim.is_confirmed:
                            continue
                        if claim.confirmed_at > self.as_of:
                            continue
                        resolution = self.resolver.resolve(
                            se.window.sku, ev.exposure.region, attr,
                            ev.exposure.seen_at, self.as_of,
                        )
                        if (resolution.fact is not None
                                and resolution.is_support
                                and resolution.fact.team == claim.team):
                            per_attr_claim[attr] = claim
                needed = len(ev.exposure.conditions)
                hit_ok = (
                    logic == "并" and bool(per_attr_claim)
                ) or (
                    logic == "交" and 0 < needed == len(per_attr_claim)
                )
                if not hit_ok:
                    continue
                for claim in per_attr_claim.values():
                    if claim.claim in seen_claims:
                        continue
                    seen_claims.add(claim.claim)
                    spillover = claim.topic != unit.topic
                    team_hits.setdefault(claim.team, []).append(
                        (claim, claim.confirmed_at, spillover)
                    )
        if not team_hits:
            self._mark_in_transit(unit)
            unit.note = "可筛但无确认认领，目标不计团队归功"
            return
        if len(team_hits) == 1:
            self._assign_single_team(unit, team_hits)
            return
        if logic == "并":
            # 并集下任一认领都能独立补上缺口，不产生共同贡献；
            # 多团队同刻治理时按确认先后，无法分辨则用团队次序定一个
            # 单独归属，避免把独立可补的功劳记成共同。
            self._assign_by_confirmation_order(unit, team_hits,
                                               allow_joint=False)
        else:
            # 交集：两个条件分属不同团队，单元本身就是共同贡献；
            # 同刻无法分辨先后时双方共同，可分辨时先完成者单独。
            self._assign_by_confirmation_order(unit, team_hits,
                                               allow_joint=True)

    def _assign_single_team(self, unit, team_hits):
        team = next(iter(team_hits))
        hits = team_hits[team]
        claim_ids = sorted({c.claim for c, _, _ in hits})
        spillover = all(sp for _, _, sp in hits)
        unit.credits[team] = (claim_ids[0], SOLE, spillover)
        self._carry_in_transit(unit, claim_ids)
        if spillover:
            unit.note = "由其他专题登记的共用事实外溢，不计本专题目标"

    def _assign_by_confirmation_order(self, unit, team_hits, *,
                                      allow_joint: bool):
        earliest = min(
            min(ts for _, ts, _ in hits) for hits in team_hits.values()
        )
        earliest_teams = sorted(
            team for team, hits in team_hits.items()
            if min(ts for _, ts, _ in hits) == earliest
        )
        if len(earliest_teams) == 1 or not allow_joint:
            team = earliest_teams[0]
            hits = team_hits[team]
            claim_ids = sorted({c.claim for c, _, _ in hits})
            spillover = all(sp for _, _, sp in hits)
            unit.credits[team] = (claim_ids[0], SOLE, spillover)
            self._carry_in_transit(unit, claim_ids)
            unit.note = "重叠治理按最早确认先后归功"
        else:
            for team in earliest_teams:
                hits = team_hits[team]
                claim_ids = sorted({c.claim for c, _, _ in hits})
                spillover = all(sp for _, _, sp in hits)
                unit.credits[team] = (claim_ids[0], JOINT, spillover)
            self._carry_in_transit(
                unit,
                [c.claim for hits in team_hits.values()
                 for c, _, _ in hits],
            )

    def _carry_in_transit(self, unit: GapUnit, credited_claim_ids: list[str]):
        self._mark_in_transit(unit)
        unit.in_transit_claims = [
            c for c in unit.in_transit_claims if c not in credited_claim_ids
        ]

    def _build_ledgers(self) -> dict[str, TeamLedger]:
        ledgers: dict[str, TeamLedger] = {}
        for unit in self.units.values():
            for team, (claim_id, shape, spillover) in unit.credits.items():
                ledger = ledgers.setdefault(team, TeamLedger(team))
                target = ledger.joint_units if shape == JOINT else ledger.sole_units
                target.add(unit.key)
                bucket = ledger.by_topic.setdefault(
                    unit.topic, {SOLE: 0, JOINT: 0, DISPUTED: 0,
                                 "spillover": 0}
                )
                if spillover:
                    bucket["spillover"] += 1
                else:
                    bucket[shape] += 1
            for team in {
                self.bundle_lookup_team(c)
                for c in unit.disputed_claims
                if self.bundle_lookup_team(c)
            }:
                ledger = ledgers.setdefault(team, TeamLedger(team))
                ledger.disputed_units.add(unit.key)
                ledger.by_topic.setdefault(
                    unit.topic, {SOLE: 0, JOINT: 0, DISPUTED: 0,
                                 "spillover": 0}
                )[DISPUTED] += 1
            for claim_id in unit.in_transit_claims:
                team = self.bundle_lookup_team(claim_id)
                if team:
                    ledgers.setdefault(team, TeamLedger(team)) \
                        .in_transit_claims.add(claim_id)
        return ledgers

    def bundle_lookup_team(self, claim_id: str) -> str:
        for claim in self.bundle.accepted:
            if claim.claim == claim_id:
                return claim.team
        return ""

    # ------------------------------------------------------------------
    def topic_attainment(self) -> dict[str, dict]:
        """各专题目标达成度：本专题确认认领覆盖的专题单元 / 总数。

        跨专题外溢的共用事实不计入本专题达成度，单列 spillover，避免把
        别的专题治理成果当成本专题补上的可筛货品。
        """
        result: dict[str, dict] = {}
        for unit in self.units.values():
            row = result.setdefault(unit.topic, {
                "topic": unit.topic, "units": 0, "credited": 0,
                "sole": 0, "joint": 0, "spillover": 0, "gaps": 0,
                "in_transit": 0,
            })
            row["units"] += 1
            primary = unit.primary_credits
            if primary:
                row["credited"] += 1
                if any(shape == JOINT for _, shape, _ in primary.values()):
                    row["joint"] += 1
                else:
                    row["sole"] += 1
            if unit.credits and not primary:
                row["spillover"] += 1
            if unit.is_gap:
                row["gaps"] += 1
            if unit.in_transit_claims:
                row["in_transit"] += 1
        for row in result.values():
            row["attainment"] = (
                round(row["credited"] / row["units"], 4)
                if row["units"] else 0.0
            )
        return result

    def group_reduction(self) -> dict:
        """治理群组口径：页面去重，归功加总不超过联合减少的曝光缺口。

        联合减少 = 当前可筛且至少有一个确认认领支撑（含跨专题外溢）的
        页面去重单元；团队页面归功之和不得超过该联合减少数。
        """
        page_units: dict = {}
        for unit in self.units.values():
            pkey = (unit.key[0], unit.key[1], unit.key[2], unit.region)
            entry = page_units.setdefault(pkey, {
                "key": pkey, "filterable": False,
                "teams": set(), "claims": set(),
                "disputed": set(),
            })
            if unit.status == FILTERABLE:
                entry["filterable"] = True
            for team, (claim_id, _shape, _spillover) in unit.credits.items():
                entry["teams"].add(team)
                entry["claims"].add(claim_id)
            entry["disputed"].update(unit.disputed_claims)
        reduced = [e for e in page_units.values()
                   if e["filterable"] and e["teams"]]
        team_page_credit: dict[str, int] = {}
        for entry in reduced:
            for team in entry["teams"]:
                team_page_credit[team] = team_page_credit.get(team, 0) + 1
        team_credit_sum = sum(team_page_credit.values())
        return {
            "joint_reduced_page_units": len(reduced),
            "total_page_units": len(page_units),
            "team_page_credit": team_page_credit,
            "team_credit_sum": team_credit_sum,
            "cap_respected": team_credit_sum <= len(reduced),
        }

    def open_gaps(self) -> list[GapUnit]:
        return [u for u in self.units.values() if u.is_gap]


def diff_snapshots(previous: Settlement, current: Settlement) -> dict:
    """归属更正：对照两个周结快照，定位有争议的旧目标贡献。

    旧周结事实截面保留；新周结逐笔检查旧归功单元当前是否仍被同一认领
    支撑。交集共同贡献在一方证据撤回时整笔重审；并集下若另一条件已让
    单元保持可筛，标记规则自愈，不重复派活。
    """
    reopened: list[dict] = []
    self_healed: list[dict] = []
    reallocated: list[dict] = []
    for key, old_unit in previous.units.items():
        if not old_unit.credits:
            continue
        new_unit = current.units.get(key)
        if new_unit is None:  # pragma: no cover - 同资料键应稳定
            continue
        for team, (claim_id, shape, spillover) in list(
                old_unit.credits.items()):
            still = any(
                cid == claim_id
                for cid, _shape, _sp in new_unit.credits.values()
            )
            if still:
                continue
            # 在新单元登记争议，供台账输出重审数。
            if claim_id not in new_unit.disputed_claims:
                new_unit.disputed_claims.append(claim_id)
            record = {
                "unit": key, "topic": old_unit.topic,
                "region": old_unit.region, "page": old_unit.page,
                "claim": claim_id, "team": team, "shape": shape,
                "spillover": spillover,
                "old_status": old_unit.status, "new_status": new_unit.status,
                "new_credits": {
                    t: list(v) for t, v in new_unit.credits.items()
                },
                "in_transit": list(new_unit.in_transit_claims),
            }
            if new_unit.is_gap:
                record["reason"] = (
                    "原支撑证据被撤或更正，已结算缺口重新打开，整笔重审"
                )
                reopened.append(record)
            elif new_unit.credits:
                record["reason"] = "旧贡献有争议，新截面改由其他认领支撑"
                reallocated.append(record)
            else:
                record["reason"] = (
                    "切换到并集规则后其他条件已自行补上，无需重复治理"
                )
                self_healed.append(record)
    # 让新结算的台账纳入争议标记。
    current.ledgers = current._build_ledgers()
    return {
        "reopened": reopened,
        "reallocated": reallocated,
        "self_healed": self_healed,
    }
