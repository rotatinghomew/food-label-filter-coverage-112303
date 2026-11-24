"""CSV 资料到领域对象的加载层，全部为合成数据的只读投影。"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .timeutil import parse_dt, parse_opt_dt

# 声明三态：支持可用于筛选；无声明是已判定的否定证据；未知是证据未判定。
STATE_SUPPORT = "支持"
STATE_NONE = "无声明"
STATE_UNKNOWN = "未知"
EVIDENCE_STATES = frozenset({STATE_SUPPORT, STATE_NONE, STATE_UNKNOWN})

LOGIC_UNION = "并"
LOGIC_INTERSECT = "交"


@dataclass(frozen=True)
class SellableWindow:
    page: str
    sku: str
    region: str
    begin: object
    end: object  # 右端不含
    family: str
    map_version: str

    def covers(self, at) -> bool:
        return self.begin <= at < self.end


@dataclass(frozen=True)
class ClaimFact:
    fact: str
    sku: str
    region: str
    attribute: str
    state: str
    start: object
    known: object
    team: str  # 空串表示无归属团队


@dataclass(frozen=True)
class TopicRule:
    topic: str
    version: int
    page: str
    change_at: object
    logic: str
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class Exposure:
    event: str
    session: str
    split: str
    page: str
    topic: str
    region: str
    seen_at: object
    conditions: tuple[str, ...]

    @property
    def page_key(self) -> tuple[str, str, str, str]:
        """跨专题合计的页面去重键，沿用日志 split 分流键并保留地区。

        同一会话可在不同地区看到同一页面，而地区可售窗与声明事实不同，
        不能跨地区合并。
        """
        return self.split, self.session, self.page, self.region

    @property
    def topic_key(self) -> tuple[str, str, str, str, str]:
        """专题内按会话、页面、地区去重的键。"""
        return self.split, self.session, self.page, self.topic, self.region


@dataclass(frozen=True)
class AcceptedClaim:
    claim: str
    team: str
    sku: str
    region: str
    attribute: str
    topic: str
    accepted_at: object
    confirmed_at: object | None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


@dataclass(frozen=True)
class CloseRecord:
    week: str
    known_through: object
    issued_at: object
    recipient: str


@dataclass(frozen=True)
class ChangeWindow:
    window: str
    region: str
    opens: object
    closes: object
    same_team: str
    accepted_claim: str  # 空串表示未接受计划
    capacity: int


@dataclass(frozen=True)
class Standard:
    region: str
    min_coverage: float
    allocation_group: str


@dataclass
class DataBundle:
    availability: list[SellableWindow]
    claims: list[ClaimFact]
    topics: list[TopicRule]
    exposures: list[Exposure]
    accepted: list[AcceptedClaim]
    closes: list[CloseRecord]
    windows: list[ChangeWindow]
    standards: list[Standard]
    source_dir: Path = field(default_factory=lambda: Path("."))


def _rows(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        for row in reader:
            yield row


def load_bundle(directory: str | Path) -> DataBundle:
    directory = Path(directory)
    availability = [
        SellableWindow(
            page=r["page"], sku=r["sku"], region=r["region"],
            begin=parse_dt(r["begin"]), end=parse_dt(r["end"]),
            family=r["family"], map_version=r["map_version"],
        )
        for r in _rows(directory / "availability.csv")
    ]
    claims = [
        ClaimFact(
            fact=r["fact"], sku=r["sku"], region=r["region"],
            attribute=r["attribute"], state=r["state"],
            start=parse_dt(r["start"]), known=parse_dt(r["known"]),
            team=r["team"].strip(),
        )
        for r in _rows(directory / "claims.csv")
    ]
    topics = []
    for r in _rows(directory / "topics.csv"):
        attrs = tuple(a for a in r["attributes"].split("/") if a)
        topics.append(
            TopicRule(
                topic=r["topic"], version=int(r["version"]), page=r["page"],
                change_at=parse_dt(r["change_at"]), logic=r["logic"],
                attributes=attrs,
            )
        )
    exposures = [
        Exposure(
            event=r["event"], session=r["session"], split=r["split"],
            page=r["page"], topic=r["topic"], region=r["region"],
            seen_at=parse_dt(r["seen_at"]),
            conditions=tuple(c for c in r["conditions"].split("/") if c),
        )
        for r in _rows(directory / "exposures.csv")
    ]
    accepted = [
        AcceptedClaim(
            claim=r["claim"], team=r["team"], sku=r["sku"],
            region=r["region"], attribute=r["attribute"], topic=r["topic"],
            accepted_at=parse_dt(r["accepted_at"]),
            confirmed_at=parse_opt_dt(r["confirmed_at"]),
        )
        for r in _rows(directory / "accepted.csv")
    ]
    closes = [
        CloseRecord(
            week=r["week"], known_through=parse_dt(r["known_through"]),
            issued_at=parse_dt(r["issued_at"]), recipient=r["recipient"],
        )
        for r in _rows(directory / "close.csv")
    ]
    windows = [
        ChangeWindow(
            window=r["window"], region=r["region"],
            opens=parse_dt(r["opens"]), closes=parse_dt(r["closes"]),
            same_team=r["same_team"], accepted_claim=r["accepted_claim"].strip(),
            capacity=int(r["capacity"]),
        )
        for r in _rows(directory / "windows.csv")
    ]
    standards = [
        Standard(
            region=r["region"], min_coverage=float(r["min_coverage"]),
            allocation_group=r["allocation_group"],
        )
        for r in _rows(directory / "standards.csv")
    ]
    return DataBundle(
        availability=availability, claims=claims, topics=topics,
        exposures=exposures, accepted=accepted, closes=closes,
        windows=windows, standards=standards, source_dir=directory,
    )
