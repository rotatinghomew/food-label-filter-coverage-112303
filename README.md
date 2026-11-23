# 线上食品筛选的覆盖缺口与治理归功

当前资料覆盖糖声明筛选专题曝光、责任认领与治理周结。记录均为合成数据，使用 UTF-8 编码及带列名的 CSV。

## 运行

使用 Python 3.11 标准库。在项目根目录查看文件列和记录数，并核对交接格式。

```sh
python3 inspect_inputs.py data
python3 -m unittest discover -s tests -v
```

## 记录约定

时间统一为上海当地时间，时区在表外固定。可售窗右端不含。展示页命中筛选需有可售规格与有效映射，所选多个条件按当时配置的并集或交集在同一规格上判断。exposures 的会话由 split 和 session 联合标识，同一会话可经过多个专题。资料确实无声明、未知、存在支持三种事实由 claims 的 state 指明。贡献确认与生效均须发生后才算生效，日期精确到小时。

- `availability.csv` 当前商品页覆盖的可售规格及映射版本。
- `claims.csv` 声明按规格、地区及生效时刻，空值不等于否。
- `topics.csv` 多专题版本，配置 change_at 之前的曝光仍用其旧规则。
- `exposures.csv` 分流会话的筛选和曝光记录，conditions 用斜杠分隔。
- `accepted.csv` 已认领治理范围与确认状态。
- `close.csv` 已做周结的时点及接收方。
- `windows.csv` 可改版时间窗及已接受计划，same_team 相同的任务在重合时段只能做一件。
- `standards.csv` 合同的地区覆盖目标，allocation_group 相同的窗口共享改版计划。
