# 技能薪酬方案的分布影响业务服务

现有工资按确认班次和当日技能时薪计算，跨院劳动在个人汇总时合并、在院区成本中分别计入。发放台账同时记录收入所属月和现金月，带教奖励仅归导师且单独确认。policies 为本地虚构的保底、年度奖励封顶与披露约定，不代表真实法律或工资政策。`GET /api/earnings?month=2026-10`、`GET /api/payments?month=2026-10&basis=cash_month` 查询已有核算；`POST /api/payment` 登记实付。

## 方案比较（不改动真实工资单）

方案是独立的记录类型，测算只读取既有劳动记录，不会写入或修改任何真实工资数据。

- `GET /api/schemes`、`POST /api/scheme`：起草方案。方案含生效日与规则（按等级费率、技能津贴、带教上浮百分点、保底/封顶覆盖、现金滞后月数、工时分档），可设年度预算上限。已送审方案保留计算起点，不可再修改，需要调整时另建方案编号。
- `GET /api/simulation?scheme=skill2026&year=2026`：在同一套实际劳动记录上逐人比较方案与现状。结果包括：
  - `people`：逐人基线、方案、差异及来源分解（`base_rule`、`skill`、`teaching`、`floor`、`cap`），并汇总为工时、技能结构、分配规则三类驱动（`drivers`），逐人各项之和恒等于总差异；
  - `by_earning_month` 与 `by_cash_month`：收入所属月与现金月两个视角，现金月按方案滞后月数平移，跨年的递延标记 `spillover`；方案生效日之前的劳动按现有规则计，技能等级按班次当天生效等级取，取得前后不混算；
  - `site_costs`：个人累计跨院合并，院区成本仍按承担方追溯；带教成本按导师当月工时摊入承担院区，当月无班次时归入 central（院本部）；
  - `composition`：基线与方案各自的收入构成（基础、保底补齐、技能津贴、带教、封顶暂扣），保底按人按月跨院合并后补齐，年度奖励封顶按人按年累计，均按 policies 的虚构约定解释；
  - `groups` 与 `disclosure`：内部全量分组与对外披露视图。人数低于 `disclosure_min_people` 的分组、或基数低于比例下限的对象，不展示金额与比例，避免夸张比例和识别个人；汇总总额仍包含这些群体，内部预算可对账；
  - `budget`：年度资金压力（收入所属月口径与现金月口径分列）。预算上限不足时给出 `suggested_variant`——按增量等比收缩的一种可执行方案，仅展示、需另存后测算，工具不按任何公平定义代选方案；
  - `reconciliation`：人员、收入月、现金月、院区、分解五个口径的总额对账。
- `GET /api/compare?schemes=a,b&year=2026`：多个可执行方案的受益分布与资金压力并列比较，不排序、不推荐。
- `GET /api/cohort?from=2026-09&to=2026-10`：观察数据的平衡面板，只含两月均在册人员；加入者与离开者单独列示，人员更替效应不计入政策效果。

## 送审与更正对账

- `POST /api/submission`（`{"scheme": "skill2026", "year": 2026}`）：送审并冻结方案快照、输入数据版本指纹与测算结果，三者共同构成计算起点。`GET /api/submissions`、`GET /api/submission?id=skill2026:2026` 查询。
- `GET /api/reconcile?submission=skill2026:2026`：工资更正到来后，用冻结的方案快照在当前数据上重算，逐人说明新增差异及其归属记录；payments 更正只影响实付对账、不改变测算。原送审结果保持不变，不被覆盖。

## 启动与核对

使用 Python 3.11 或更高版本。现有服务采用标准库运行；数值分析所需的固定版本工具列在 requirements.txt，部署准备时运行 `python3 -m pip install -r requirements-build.txt -r requirements.txt`，构建前端和打包后端的固定版本见 requirements-build.txt；依赖准备完毕后，测试、构建和运行均可断网进行。无需真实个人信息、凭据或远程数据源。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q paypolicy tests
python3 -m build --wheel --no-isolation
python3 -m paypolicy check
python3 -m paypolicy demo
python3 -m paypolicy load --database /tmp/paypolicy-business.sqlite3 --input examples/operations.json
python3 -m paypolicy serve --database /tmp/paypolicy-business.sqlite3
```

服务默认监听本机 8765 端口，`--port` 可指定其他端口。健康检查为 `GET /health`，`GET /api/summary` 返回当前已有业务汇总（含草案方案的分布影响）。未指定数据库时，demo 和 check 使用隔离的临时数据库；serve 若需保留修改须传入数据库路径。已有数据库不重复装载同一类样例。examples/operations.json 是较大的一组历史运营资料，可用 load 命令在事务内载入；按类型和编号更正同名记录，未出现的记录保持不变。所有资料采用固定来源生成且不包含对未来分析结论的预设值。

## 数据与运行边界

fixtures 中均为本地合成业务资料，员工、家庭和院区编号不对应真实对象；schemes.json 是示例草案方案。JSON 里的空值表示未记录，不代表零；日期采用 ISO 格式。金额以分保存，工时以分钟保存。每类记录有独立编号，写入同编号用于业务更正，revision 用于并发修改核对。

业务状态由 SQLite 保存，记录批量更正在单一事务内提交。接口使用 UTF-8 JSON，未知资源返回 404，非法数据返回 400，版本冲突返回 409。服务不进行临床诊断，也不自动改变实际服务安排。测试使用临时数据库和系统分配的空闲端口，彼此隔离，不访问公网。运行日志、数据库和依赖缓存不应提交到仓库。
