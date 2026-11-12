# 支付凭单生成台

按《支 出 凭 单.docx》母版生成支付凭单（docx），支持单张录入与批量生成（多凭单合并为一个文件、每张一页）、版式预览（与输出一致）、考勤工资测算与工资测算依据表导出、历史记录与模板复用。

## 运行

```bash
python3 -m pip install -r requirements.txt
createdb payment_voucher          # 按需创建数据库
python3 app.py                    # http://127.0.0.1:5000
```

- 首次访问会进入初始化页，设置管理员账号与密码；之后所有页面与接口都需要登录。
- 数据库连接信息默认为空，需登录后到「设置 → 数据库连接」填写并保存。配置以 Fernet 加密存放在 `db_config.json`（密钥为同目录 `secret.key`），密码不落明文；也可用环境变量 `PV_DB_HOST / PV_DB_PORT / PV_DB_NAME / PV_DB_USER / PV_DB_PASSWORD` 覆盖。
- 保存连接信息时自动测试连接并建表，写入默认身份、费用类型字典。
- 人员库在「设置」页点击「导入表格文件…」，支持 `xlsx / xlsm / xls / csv / tsv`，表头需包含“姓名”列。

## 结构

| 文件 | 职责 |
| --- | --- |
| `app.py` | Flask 页面路由与 REST 接口（登录、预览、凭单、批量生成、考勤测算、模板、字典、人员导入） |
| `security.py` | 密钥管理、数据库配置与管理员凭据的加密存储 |
| `connectdb.py` | PostgreSQL 连接配置与 `get_conn()` 上下文管理器 |
| `db.py` | 建表 SQL 与全部增删改查函数 |
| `voucher.py` | 金额大写、预览上下文、按母版生成 docx |
| `templates/` | Jinja 页面，`_paper.html` 为 A4 凭单预览组件 |
| `static/` | 样式与交互脚本 |
| `output/` | 生成的 docx 文件 |
| `支 出 凭 单.docx` | 版式母版，生成时复制后替换文字，不改动版式 |

## 生成逻辑

1. 表单 → `POST /api/preview` → `voucher.build_context()` 拼出「即付」正文、大写金额 → 渲染 `_paper.html` 供预览。
2. 确认后 `POST /api/vouchers` 落库（凭单号为 `voucher_no_seq` 序列，记录为录入时快照）。
3. `GET /api/vouchers/<id>/docx` → `voucher.build_docx()` 复制母版、替换标题行/正文表格四个段落的文字（楷体 14 磅，金额符号与数字用宋体），另存到 `output/`。替换时复用母版 run 的格式标签，**虚线与下划线保持原样**。

母版文件名含不间断空格（`支\xa0出\xa0凭\xa0单.docx`），`voucher.template_path()` 按文件名关键字匹配，勿按固定字符串拼接路径。
