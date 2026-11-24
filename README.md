# 线上食品筛选的覆盖缺口与治理归功

当前资料覆盖糖声明筛选专题曝光、责任认领与治理周结。记录均为合成数据，使用 UTF-8 编码及带列名的 CSV。

## 运行

使用 Python 3.11 标准库。在项目根目录查看文件列和记录数，并核对交接格式。

```sh
python3 inspect_inputs.py data
python3 -m unittest discover -s tests -v
# 生成周结（JSON + Markdown），-I 隔离模式下数据目录只作为参数传入
python3 -I run_weekly.py data
# 独立交叉核算，与主引擎分开实现以发现系统性偏差
python3 -I scripts/crosscheck.py data
```

周结默认资料截止 `2026-09-07T05:00`，可用 `--as-of` 重算历史截面，
用 `--json-out` / `--md-out` 改输出位置。

## 周结引擎（weekly 包）

- `timeutil` / `model`：时间解析、ISO 周标签与 CSV 到领域对象的只读投影。
- `facts`：声明三态（支持/无声明/未知）的时点解析。只有 `known <= as_of`
  的条目登记在案，取最新登记且要求 `start` 已生效；无登记是资料空白，
  不等于否定。
- `catalog`：地区可售窗、同配方族换码、专题规则版本（change_at 之前用旧规则）。
- `coverage`：曝光级判定与两套去重口径。
  - 页面口径 `(split, session, page, region)`：跨专题合计，地区不合并。
  - 专题口径 `(split, session, page, topic, region)`：专题各自统计。
  - 多条件在同一规格上按当时界面的并/交判断；日志所选条件允许是专题配置子集。
  - 非可筛按 证据未判定 > 资料空白 > 已判定无声明 归桶，另列无可售规格。
- `settlement`：认领结算。确认且生效才计入；仅登记未确认保留在途不充目标。
  认领按事实键匹配、不限登记专题，跨专题外溢单列且不充该专题目标；
  并集只产生单独归功（同刻按团队次序），交集才产生共同贡献。
  `diff_snapshots` 保留旧周结事实截面并逐笔定位有争议的旧目标贡献。
- `planning`：只在真实窗口内枚举改派/合做方案，保留既有窗口承诺，
  same_team 重合时段互斥；并集单元完成任一阻塞工作即补上，交集需全部完成；
  已判定无声明标记为不可资料治理；对照合同各区最低覆盖并解释缺窗口还是缺事实。
- `report` / `cli`：装配 JSON 与给专题负责人、运营主管的 Markdown 周结。

## 记录约定

时间统一为上海当地时间，时区在表外固定。可售窗右端不含。展示页命中筛选需有可售规格与有效映射，所选多个条件按当时配置的并集或交集在同一规格上判断。exposures 的会话由 split 和 session 联合标识，同一会话可经过多个专题、多个地区。资料确实无声明、未知、存在支持三种事实由 claims 的 state 指明。贡献确认与生效均须发生后才算生效，日期精确到小时。

- `availability.csv` 当前商品页覆盖的可售规格及映射版本。
- `claims.csv` 声明按规格、地区及生效时刻，空值不等于否。
- `topics.csv` 多专题版本，配置 change_at 之前的曝光仍用其旧规则。
- `exposures.csv` 分流会话的筛选和曝光记录，conditions 用斜杠分隔。
- `accepted.csv` 已认领治理范围与确认状态。
- `close.csv` 已做周结的时点及接收方。
- `windows.csv` 可改版时间窗及已接受计划，same_team 相同的任务在重合时段只能做一件。
- `standards.csv` 合同的地区覆盖目标，allocation_group 相同的窗口共享改版计划。
