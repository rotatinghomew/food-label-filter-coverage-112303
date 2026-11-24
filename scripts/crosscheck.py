"""独立交叉核算（与主引擎分开写，便于发现系统性偏差）。"""
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def dt(s):
    return datetime.fromisoformat(s if "T" in s else s + "T00:00")


def load(name):
    with (Path(sys.argv[1]) / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


data = Path(sys.argv[1])
avail = load("availability.csv")
claims = load("claims.csv")
topics = load("topics.csv")
expos = load("exposures.csv")
as_of = dt("2026-09-07T05:00")


def sellable(page, region, at):
    out = []
    for r in avail:
        if r["page"] == page and r["region"] == region and dt(r["begin"]) <= at < dt(r["end"]):
            out.append(r["sku"])
    return out


def fact_state(sku, region, attr, at, cutoff):
    rows = [r for r in claims if r["sku"] == sku and r["region"] == region
            and r["attribute"] == attr and dt(r["known"]) <= cutoff]
    if not rows:
        return None, None
    rows.sort(key=lambda r: dt(r["known"]))
    latest = rows[-1]
    return latest, (latest["state"] == "支持" and dt(latest["start"]) <= at)


def rule(topic, page, at):
    rows = [r for r in topics if r["topic"] == topic and r["page"] == page]
    rows.sort(key=lambda r: dt(r["change_at"]))
    cur = rows[0]
    for r in rows:
        if dt(r["change_at"]) <= at:
            cur = r
    return cur


page_units = {}
topic_units = defaultdict(list)
status_detail = []
for e in expos:
    at = dt(e["seen_at"])
    conds = e["conditions"].split("/")
    tr = rule(e["topic"], e["page"], at)
    skus = sellable(e["page"], e["region"], at)
    if not skus:
        status = "无可售规格"
    else:
        sku_ok = []
        fail_states = []
        for sku in skus:
            per = {}
            for c in conds:
                row, ok = fact_state(sku, e["region"], c, at, as_of)
                per[c] = (row, ok)
            supports = {c: v[1] for c, v in per.items()}
            match = any(supports.values()) if tr["logic"] == "并" else all(supports.values())
            sku_ok.append(match)
            if not match:
                for c, (row, ok) in per.items():
                    if ok:
                        continue
                    if row is None:
                        fail_states.append("资料空白")
                    elif row["state"] == "未知" or dt(row["start"]) > at:
                        fail_states.append("证据未判定")
                    elif row["state"] == "无声明":
                        fail_states.append("已判定无声明")
        status = "可筛" if any(sku_ok) else (
            "证据未判定" if "证据未判定" in fail_states
            else "资料空白" if "资料空白" in fail_states
            else "已判定无声明"
        )
    pk = (e["split"], e["session"], e["page"], e["region"])
    tk = (e["split"], e["session"], e["page"], e["topic"], e["region"])
    rank = {"可筛": 4, "证据未判定": 3, "资料空白": 2, "已判定无声明": 1, "无可售规格": 0}
    if pk not in page_units or rank[status] > rank[page_units[pk]]:
        page_units[pk] = status
    topic_units[tk].append(status)
    status_detail.append((e["event"], tk, status))

print("=== 页面去重口径（独立核算） ===")
cnt = defaultdict(int)
for s in page_units.values():
    cnt[s] += 1
for k, v in sorted(cnt.items()):
    print(f"{k}: {v} ({v/len(page_units):.4f})")
print("总计:", len(page_units))

print("\n=== 专题去重口径 ===")
tcnt = defaultdict(lambda: defaultdict(int))
for tk, states in topic_units.items():
    topic = tk[3]
    merged = max(states, key=lambda s: rank[s])
    tcnt[topic][merged] += 1
for topic in sorted(tcnt):
    print(topic, dict(tcnt[topic]), "总", sum(tcnt[topic].values()))

print("\n=== 单声明口径（页面去重，任意可售规格支持） ===")
for attr in ("未加蔗糖", "总糖已标"):
    yes = set()
    allk = set()
    for e in expos:
        at = dt(e["seen_at"])
        pk = (e["split"], e["session"], e["page"], e["region"])
        allk.add(pk)
        for sku in sellable(e["page"], e["region"], at):
            _, ok = fact_state(sku, e["region"], attr, at, as_of)
            if ok:
                yes.add(pk)
    print(attr, len(yes), "/", len(allk), round(len(yes)/len(allk), 4))

print("\n=== T2 版本切换前后 ===")
v = defaultdict(lambda: defaultdict(int))
seen_unit_ver = {}
for e in expos:
    if e["topic"] != "T2":
        continue
    at = dt(e["seen_at"])
    tr = rule("T2", e["page"], at)
    tk = (e["split"], e["session"], e["page"], "T2", e["region"])
    states = topic_units[tk]
    merged = max(states, key=lambda s: rank[s])
    ver = f"v{tr['version']}-{tr['logic']}"
    # 一个单元可能跨版本事件，按其首个事件版本归类一次。
    if tk not in seen_unit_ver:
        seen_unit_ver[tk] = ver
        v[ver][merged] += 1
for ver, row in sorted(v.items()):
    print(ver, dict(row))

print("\n=== 旧截面 as_of=2026-08-31T13:00 的事件状态（抽查关键事件） ===")
old_cut = dt("2026-08-31T13:00")
for eid in ("e0", "e1", "e2", "e6", "e15", "e31", "e16"):
    e = next(x for x in expos if x["event"] == eid)
    at = dt(e["seen_at"])
    conds = e["conditions"].split("/")
    tr = rule(e["topic"], e["page"], at)
    skus = sellable(e["page"], e["region"], at)
    detail = []
    for sku in skus:
        for c in conds:
            row, ok = fact_state(sku, e["region"], c, at, old_cut)
            detail.append((sku, c, row["fact"] if row else None,
                           row["state"] if row else None, ok))
    print(eid, e["topic"], tr["logic"], e["seen_at"], detail)
