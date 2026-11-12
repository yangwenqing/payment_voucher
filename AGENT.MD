# AGENT.MD This file provides guidance to CodeBuddy when working with code in this repository.

## 目标
支付凭单生成系统：录入/批量生成支付凭单（预览版式与 docx 输出一致），考勤页实时测算工资并可导出工资测算依据表。输出样式以目录下的《支 出 凭 单.docx》母版为准。

## 常用命令

```bash
python3 -m pip install -r requirements.txt     # 安装依赖（Flask、psycopg3、python-docx、openpyxl、xlrd、cryptography）
createdb payment_voucher                       # 按需创建数据库（本机 PostgreSQL）
python3 app.py                                 # 启动，http://127.0.0.1:5000
```

- 首次访问 `/login` 进入初始化模式：设置管理员账号与密码（PBKDF2 摘要存储），之后所有页面与接口都需登录会话。
- 数据库连接信息不再有默认值，需在「设置 → 数据库连接」填写并保存（Fernet 加密写入 `db_config.json`）。
- 单页冒烟测试（不需要浏览器）：`python3 -c "import app; c=app.app.test_client(); print(c.get('/').status_code)"` → 302 跳登录
- 建表/补默认字典：`python3 -c "import db; db.init_schema()"`（需先配置数据库）
- 金额大写自检：`python3 -c "import voucher; print(voucher.amount_to_chinese('10512.5'))"` → `壹万零伍佰壹拾贰元伍角`
- 生成 docx 自检：`python3 -c "import voucher; print(voucher.build_docx({'serial_no':1,'voucher_date':'2026-10-27','name':'张宇浩','identity':'社区专家','bank_name':'招商银行','bank_account':'6214','amount':'10512.5','fee_type':'劳务费用','period_month':'10月','purpose':'采购'}))"`
- 前端语法检查：`node --check static/app.js`

无测试框架，验证方式是 Flask `test_client` 打接口 + 回读生成的 docx 文本。

## 架构

三层，数据流单向：浏览器表单 → `/api/preview` 渲染预览 → `/api/vouchers` 落库 → `/api/vouchers/<id>/docx` 出文件；批量走 `/batch` 勾选人员 → `/api/vouchers/batch` → 合并多页 docx；考勤走 `/attendance` 实时保存并测算工资。

- `app.py`：仅做 HTTP 层。页面路由（`/`、`/batch`、`/attendance`、`/history`、`/templates`、`/settings`）与 JSON 接口；`before_request` 统一做登录校验（接口返回 401，页面 302 到 `/login`）；`_payload()` 把空串转 `None`，`_serialize()` 把 `Decimal`/`date` 转成 JSON 友好值；`ValueError` → 400、`RuntimeError`/`psycopg.Error` → 503 `{"error": ...}`。数据库不可用时页面仍可打开，模板上下文里的 `db_ok`/`db_ready` 用于顶部告警。表格导入拆为 `_read_table_rows()`（xlsx/xlsm/xls/csv/tsv）与 `_persons_payload()`（前 10 行内自动找含“姓名”的表头）。`/api/vouchers/batch` 批量落库后用 `voucher.build_batch_docx()` 合并，`/api/batch-docx/<name>` 供下载（仅限 output 目录内 docx）。`/api/attendance/*` 提供考勤列表、实时保存、金额查询与工资测算依据表导出（openpyxl 按《工资计算依据.xlsx》版式生成）。
- `security.py`：本地加密存储。`secret.key` 为 Fernet 密钥（同时用作 Flask 会话密钥），`db_config.json` 是整文件密文，保存数据库连接参数与管理员 PBKDF2 摘要；`load_key()`/`get_db_config()`/`set_db_config()`/`verify_admin()` 供其他模块调用。
- `connectdb.py`：连接参数以加密配置为基础，环境变量（`PV_DB_HOST/PORT/NAME/USER/PASSWORD`）可覆盖；默认全部为空，`is_configured()` 为假时 `get_conn()` 抛 `RuntimeError`；`get_conn()` 为上提交/异常回滚的上下文管理器，`check_connection()` 供界面探测。
- `db.py`：建表 SQL（`SCHEMA`）+ 默认字典（`DEFAULT_IDENTITIES`、`DEFAULT_FEE_TYPES`）+ 全部增删改查。表：`identities`、`fee_types`（可扩字典）、`persons`（人员库）、`vouchers`（凭单快照）、`voucher_templates`（模板）、`attendance`（考勤与工资测算，按 person_id+period_month 唯一）。凭单号来自序列 `voucher_no_seq`，`peek_next_serial_no()` 只读不占用，供预览显示编号。凭单创建时 `fee_type` 必填；`_clean_voucher` 不再校验/写入事由（purpose 列保留但已弃用）。考勤：`compute_attendance()` 实现「线上合计 = 1500×线上÷应出勤；线下合计 = 应发薪资×线下÷应出勤；总计 = 两者+奖金」，`upsert_attendance()` 保存即算，`get_attendance_amount()` 供制单/批量页默认填充金额（费用类型为工资/技术服务费时）。
- `voucher.py`：`amount_to_chinese()` 人民币大写；`build_context()` 由一条记录拼出「即付」正文、日期、大写金额，是预览与 docx 的唯一文案来源（`line1_body` 为去掉「即    付 」前缀后的正文，供预览加虚线）；`build_docx()` 复制母版后按段落替换文字（标题行楷体 12 磅，正文楷体 14 磅，`¥` 与数字用宋体），输出到 `output/`；`build_batch_docx()` 把多张凭单合并为一个 docx，每张凭单以分页符分隔、各占一页（深拷贝母版整页元素 + `w:br type=page`，`_group_elements()` 分组后逐组 `_fill_voucher()`）。`_set_runs()` 会先 `_capture_styles()` 记录母版原 run 的格式（无下划线/虚线 DOTTED/单下划线），重建时按 `STYLE_PLAIN/DOTTED/UNDERLINE` 标签复用，并用 `_trailing_space_runs()` 原样保留段落末端的划线空白，**勿改回「删光 run 再加纯文本 run」的写法，否则下划线与虚线会丢失**。
- `templates/_paper.html`：A4 比例（`aspect-ratio: 210/297`）的凭单预览组件，同时被 `/api/preview` 以 HTML 片段返回，前端直接替换 `#previewSlot`，因此预览与导出版式共用一套文案。
- `static/app.js`：按 `window.__PAGE__` 分发到制单/批量/考勤/记录/模板/设置六套逻辑；制单页输入防抖 320ms 触发预览，`?voucher_id=` 进入修改模式，`?template_id=` 进入模板模式并锁死人员与账户字段；人员库下拉切回「手动填写」时清空已填充人员信息并恢复默认值（`resetManual`）；费用类型切到「工资/技术服务费」（`AUTO_FEE_TYPES`）且已选人员时调 `/api/attendance/amount` 默认填充金额；考勤页每行输入防抖 800ms 实时保存并在前端即时重算合计；批量页勾选人员、按行编辑费用类型与金额后统一提交 `/api/vouchers/batch`。静态资源经 `?v=<mtime>` 缓存版本加载（`inject_globals` 注入 `static_v`），前端文件更新后浏览器自动拉取最新版。

## 注意

- 母版文件名含不间断空格（`支\xa0出\xa0凭\xa0单.docx`），`voucher.template_path()` 按「含“凭”“单”的 .docx」匹配，不要拼接固定路径。
- `vouchers` 存的是录入时快照：人员库或字典改动不影响历史记录；凭单号一旦生成不随编辑变化。
- 「事由」（purpose）已从制单页、模板页与凭单正文删除，`purpose` 列仅保留兼容；「薪资」不在制单页显示，薪资来源为考勤页（按人员+月份）或模板快照，通过前端 `salaryOverride` 随表单提交。
- 凭单费用类型必填，前端下拉不允许空值（无“—”占位），制单页默认选中第一项费用类型。
- 制单页手动填写时的默认值（与模板弹窗一致）：开户行 `xxxxxx`、银行账号 `1234567890`、支付金额 `0`；金额 0 视为未填写（不渲染预览、提交拦截「支付金额必须大于 0」，后端 `_clean_voucher` 兜底 400），姓名/身份/费用类型/金额必填校验不可放松；选人员、模板模式、编辑模式 fill 时默认值被覆盖。
- 考勤公式固定为：线上合计 = 1500×线上÷应出勤，线下合计 = 应发薪资×线下÷应出勤，总计 = 线上合计+线下合计+奖金（奖金记入备注列，导出与《工资计算依据.xlsx》版式一致：姓名/{月}应出勤/请假/缺勤/线上/线上合计/线下/线下合计/总计/备注）。
- 考勤页备注栏与导出同步显示奖金：有奖金时备注框显示「奖金 N；用户备注」（`withBonusNote`），保存时前端用 `stripBonusNote` 剥离前缀、note 列只存用户备注、奖金仍单独存 `bonus` 列，导出时再拼接，三处文本格式统一（`奖金 ` + format_amount + `；`）。
- 月份统一为「YYYY年M月」（含年份标志）：`db.normalize_month()` 接受选择器值 "2026-10"、"2026年10月"、"10月"（补当前年份）等并归一化，`db.month_input_value()` 反向转选择器值；制单/考勤/批量页与模板弹窗均用 `<input type="month">` 选择器，禁止手填文本；前端用 `formatMonth()`/`monthToInput()` 转换。
- 批量生成页每行取人员库快照，生成时逐条落库取号，合并 docx 中每张凭单独占一页。
- 模板语义是「人员固定、细项可变」：模板页只允许改 `title/fee_type/salary/amount/period_month`，人员与账户字段在模板模式下禁用。
- 身份、费用类型是数据库字典，禁止在代码或前端写死；新增走 `/api/identities`、`/api/fee-types`。
- 人员库由「设置 → 导入表格文件」上传批量写入，支持 xlsx/xlsm/xls/csv/tsv（csv/tsv 自动尝试 utf-8-sig/gbk/utf-16 编码），表头在文件前 10 行内自动识别（以含“姓名”的行为准），姓名+账号重复时更新；表格可含「薪资/应发薪资/工资」列（缺失默认空）。设置页表格每行可「编辑」（弹窗走 `PUT /api/persons/<id>`，字段校验 `_clean_person`），姓名/身份必填，薪资可改。
- 薪资属性存于 `persons.salary`：考勤页「应发薪资」与之同步——考勤列表 `COALESCE(a.salary, p.salary)`（无考勤记录时回退人员库薪资），考勤保存后回写 `persons.salary`；导入或人员编辑的薪资也会作为考勤默认值。
- 模板页「编辑细项」弹窗包含银行卡号与开户行字段：模板未填写时默认 银行卡号 `1234567890`、开户行 `xxxxxx`、默认金额 `0`，保存后随模板持久化。
- 新增或迁移部署时不要提交 `secret.key` 与 `db_config.json`；删除 `secret.key` 会导致已保存配置无法解密（需重新填写连接信息与管理员账号）。
