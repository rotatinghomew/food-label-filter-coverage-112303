"""命令行入口：生成周结 JSON，并可输出 Markdown 叙述版。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model import load_bundle
from .report import STATUS_LABEL, WeeklyReport, build_report

DEFAULT_AS_OF = "2026-09-07T05:00"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_markdown(report: WeeklyReport) -> str:
    d = report.to_dict()
    lines: list[str] = []
    lines.append(f"# 糖声明筛选治理周结 {d['week']}")
    lines.append("")
    lines.append(f"- 资料截止（as_of）：{d['as_of']}")
    lines.append(
        "- 既有周结："
        + "；".join(
            f"{c['week']} 截止 {c['known_through']} 发 {c['recipient']}"
            for c in d["previous_closes"]
        )
    )
    lines.append("")

    lines.append("## 1. 覆盖实况（页面按会话×分流键去重，跨专题已去重）")
    overall = d["coverage_overall"]
    lines.append(f"- 去重页面单元：{overall['denominator']}")
    for label, count in overall["counts"].items():
        share = overall["shares"][label]
        lines.append(f"- {label}：{count}（{_fmt_pct(share)}）")
    lines.append("")
    lines.append("### 单声明可筛份额")
    for attr, block in d["coverage_by_attribute"].items():
        yes = block["counts"]["可筛"]
        lines.append(
            f"- {attr}：{yes}/{block['denominator']}（"
            f"{_fmt_pct(block['shares']['可筛'])}）"
        )
    lines.append("")
    rc = d["root_cause"]
    lines.append("### 找不到商品的两层原因")
    lines.append(f"- 可筛：{rc['可筛']}")
    lines.append(f"- 没有可售规格：{rc['无可售规格']}")
    lines.append(f"- 售卖可用但无法按糖声明筛出：{rc['可售但不可筛']}")
    for label, count in rc["可售但不可筛明细"].items():
        lines.append(f"  - {label}：{count}")
    lines.append("")

    lines.append("## 2. 专题口径（各自统计，不与页面口径混用）")
    for topic, block in sorted(d["coverage_by_topic"].items()):
        lines.append(
            f"- {topic}：去重单元 {block['denominator']}，可筛 "
            f"{block['counts']['可筛']}（{_fmt_pct(block['shares']['可筛'])}）"
        )
    for topic, versions in sorted(d["topic_versions"].items()):
        if len(versions) > 1:
            lines.append(f"- 专题 {topic} 定义跨期变化，分版本列示：")
            for v in versions:
                lines.append(
                    f"  - {v['version']}：{v['deduped_units']} 单元，"
                    f"{v['comparable']}"
                )
    lines.append("")

    lines.append("## 3. 归功与目标达成")
    for topic, row in sorted(d["attainment"].items()):
        lines.append(
            f"- {topic} 达成度：{row['credited']}/{row['units']} = "
            f"{_fmt_pct(row['attainment'])}（单独 {row['sole']}、"
            f"共同 {row['joint']}、开放缺口 {row['gaps']}、在途 {row['in_transit']}）"
        )
    group = d["group_reduction"]
    lines.append(
        f"- 治理群组联合减少的不可筛页面单元："
        f"{group['joint_reduced_page_units']}/{group['total_page_units']}"
    )
    for team, row in sorted(d["team_ledgers"].items()):
        lines.append(
            f"- 团队 {team}：单独 {row['sole_units']}、共同 {row['joint_units']}、"
            f"争议重审 {row['disputed_units']}、在途认领 "
            f"{','.join(row['in_transit_claims']) or '无'}"
        )
    lines.append("")

    lines.append("## 4. 开放缺口与在途（按治理工作归并，单元明细见 JSON）")
    lines.append(f"- 开放专题单元合计：{len(d['open_gaps'])} 个")
    for work in d["planning"]["gap_works"]:
        affected = "、".join(work["topics"])
        unit_count = len(work["blocking_units"])
        sku, region, attr = work["work"]
        flag = "不可资料治理" if not work["governable"] else "可治理"
        transit = (f"；在途 {work['existing_claim']}"
                   if work["in_transit"] else "")
        lines.append(
            f"- [{flag}] {region}/{sku}/{attr}（{work['page']}）"
            f"影响专题 {affected}，阻塞 {unit_count} 个单元："
            f"{work['block_reason']}{transit}"
        )
    for item in d["in_transit"]:
        lines.append(
            f"- 在途 {item['claim']}（{item['team']}，{item['sku']}/"
            f"{item['region']}/{item['attribute']} → {item['topic']}）："
            f"{item['reason']}"
        )
    lines.append("")

    lines.append("## 5. 归属更正与旧账重审")
    old = d["snapshot_diff"].get("old_snapshot")
    if old:
        lines.append(
            f"- 旧截面 {old['known_through']}（{'、'.join(old['week'])}）"
            f"保留原事实：{old['note']}"
        )
    for kind, title in (
        ("reopened", "重新打开的已结算缺口"),
        ("reallocated", "改归他人的旧目标贡献"),
        ("self_healed", "并集规则自愈、无需重复治理"),
    ):
        entries = d["snapshot_diff"].get(kind, [])
        if not entries:
            continue
        lines.append(f"### {title}（共 {len(entries)} 笔，下列为明细）")
        for entry in entries:
            lines.append(
                f"- 单元 {list(entry['unit'])}：认领 {entry['claim']}（"
                f"{entry['team']}，{entry['shape']}）旧状态 "
                f"{STATUS_LABEL.get(entry['old_status'], entry['old_status'])} "
                f"→ 新状态 {STATUS_LABEL.get(entry['new_status'], entry['new_status'])}"
                + (f"；{entry['reason']}" if entry.get("reason") else "")
            )
    lines.append("")

    lines.append("## 6. 改版窗口建议（只分配真实窗口）")
    for key, title in (
        ("baseline", "保留既有窗口承诺"),
        ("best_reassign", "改派试算（最优）"),
        ("best_joint", "合做试算（最优）"),
    ):
        plan = d["planning"].get(key)
        if not plan:
            continue
        lines.append(f"### {title}（{plan['mode']}）")
        lines.append(
            "- 预计联合减少："
            + "、".join(f"{r} {n} 单元" for r, n in
                       plan["expected_reduction"].items())
        )
        for region, ok in plan["meets_standard"].items():
            lines.append(
                f"- {region} 改版后覆盖 {_fmt_pct(plan['coverage_after'][region])}"
                f"，合同达标：{'是' if ok else '否'}"
            )
        for a in plan["assignments"]:
            work = "/".join(a["work"]) if a["work"] else "保留/闲置"
            lines.append(
                f"- 窗口 {a['window']}（{a['region']} "
                f"{a['opens']}~{a['closes']}）→ {a['team']} 做 {work}"
                + (f"；{a['reason']}" if a["reason"] else "")
            )
    if d["planning"]["shortages"]:
        lines.append("### 无法同时达标时的缺口解释")
        for note in d["planning"]["shortages"]:
            lines.append(f"- {note}")
    lines.append("")

    lines.append("## 7. 同配方换码对照")
    for family in d["family_continuity"]:
        codes = "、".join(
            f"{t['sku']}（{t['region']} {t['window'][0]}~{t['window'][1]} "
            f"映射 {t['map_version']}）"
            for t in family["timeline"]
        )
        lines.append(f"- 配方 {family['family']}：{codes}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成糖声明治理周结")
    parser.add_argument("data_dir", type=Path, help="CSV 资料目录")
    parser.add_argument("--as-of", default=DEFAULT_AS_OF,
                        help="资料截止时点，默认 2026-09-07T05:00")
    parser.add_argument("--json-out", type=Path,
                        default=Path("out/weekly_report.json"))
    parser.add_argument("--md-out", type=Path,
                        default=Path("out/weekly_report.md"))
    parser.add_argument("--drill-limit", type=int, default=12)
    args = parser.parse_args(argv)

    bundle = load_bundle(args.data_dir)
    report = build_report(bundle, args.as_of, drill_limit=args.drill_limit)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2,
                   default=str),
        encoding="utf-8",
    )
    args.md_out.write_text(render_markdown(report), encoding="utf-8")
    print(f"已写出 {args.json_out} 与 {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
