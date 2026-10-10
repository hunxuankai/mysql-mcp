# 使用说明

## 配置和命令

`mysql-mcp --config PATH --connection local --mode query` 启动 stdio 服务。PATH 由使用者指定，与当前工作目录和包安装目录无关；也可通过 `MYSQL_MCP_CONFIG` 提供。连接名可以自定义；省略时为 local。`python -m mysql_mcp` 使用同一入口。

`--help` 和 `--version` 不需要配置。`--check-config` 验证配置和选中模式，输出脱敏摘要后退出，不连接数据库。启动前明确配置 host、port、database、user、password。缺少密码字段拒绝，显式空密码仅用于使用者明确设置的本机实例。

只有选中的模式会暴露工具。开启写入同时需要配置 `allow_write=true` 和 `--mode write`；DDL 同理。生产连接标记 `production=true`，变更工具要求 `confirm_environment="production"`，查询不需要该参数。

可以通过 `connections.<名称>.accounts.query/write/ddl` 覆盖账号，也可以分别建立连接 profile。MCP 模式标签不是数据库授权；优先给每种模式使用对应最小权限账号。

## TLS

本机地址默认不使用 TLS，远程地址默认启用 TLS。开启后始终校验证书和主机名；没有跳过验证选项。`ca` 省略时使用系统信任；`cert`/`key` 必须成对。证书相对路径以配置文件目录为基准。

只在已经提供其他可信传输保护的隔离网络中显式设置 `enabled=false`。连接失败时修复主机名、CA 或证书，不以关闭验证作为排错步骤。

## 客户端接入

先在终端执行固定版本的 `--check-config`，完成依赖下载和本机配置检查，再加入客户端。JSON 配置见 [client.json](../examples/client.json)；不同客户端配置入口可能不同，应将整个条目合并到自己的 MCP 配置中。

Codex 使用 [codex.toml](../examples/codex.toml)，由 command 启动进程、args 传入参数。示例为首次下载预留 120 秒启动时间，单工具 30 秒，服务自身仍受 15 秒默认总期限。需要增大服务期限时，客户端工具期限也应同步调整。[官方配置来源](https://learn.chatgpt.com/docs/extend/mcp#stdio-servers)。

若桌面客户端 PATH 中没有 uvx，将 command 写成绝对路径。Windows 的 TOML/JSON 示例使用正斜杠；修改配置后由客户端重新启动对应 MCP 进程。

## SQL 支持

- query：SELECT（含普通/递归/多重/嵌套 CTE）、受控 SHOW TABLES/COLUMNS/INDEX/CREATE TABLE、DESCRIBE 和 EXPLAIN SELECT。
- write：单条 INSERT、UPDATE、DELETE、REPLACE；支持符合 MySQL 语法的 CTE。
- ddl：表和索引的 CREATE、ALTER、DROP、TRUNCATE TABLE 和简单 RENAME TABLE。
- 不支持多语句、CALL、账号/角色/授权/数据库管理、触发器/事件/存储程序、文件输入输出、执行型注释、危险提示、显式锁和睡眠/基准测试函数。

直接表引用只能属于选中的 database；包括子查询、CTE 和外键目标。未知或未被可靠解析的语法拒绝执行。策略校验与 MySQL 使用一致的引号模式，连接内移除 ANSI_QUOTES、NO_BACKSLASH_ESCAPES，保留其他 sql_mode；不会修改服务器全局模式。

双短横线注释标记后使用 ASCII 空白；为避免解析器与 MySQL 对 Unicode 空白的不同解释，不接受 `--` 后直接跟非 ASCII 字符。反引号标识符使用双反引号转义，不接受其中的反斜杠转义。SQL 文本中的 NUL 拒绝执行。

describe_table/show_create_table 的 table 参数只表示当前库中的一个表名，可以包含中文、空格和反引号；不把参数当作 SQL 片段。

**每个调用是独立连接。** 不跨调用维持事务、临时表、变量或锁。write_sql 在成功时提交；DDL 服从 MySQL 隐式提交语义。SQL 文本检查无法隔离数据库中预先部署的 UDF、触发器或 SQL SECURITY DEFINER 对象，数据库账号权限与受信任数据库管理仍是必要条件。

## 返回类型

工具的 structured_content 形如 `{"ok":true,"data":{...},"mcp_context":{...}}`，失败使用 `ok=false` 和 error 字段。兼容文本包含同一 JSON。查询 data 包含 columns、二维 rows、row_count 和 truncated。重复列名保留；不把行转换为按列名覆盖的对象。

| 数据类型 | JSON 表示 |
| --- | --- |
| DECIMAL | 精确十进制字符串 |
| 超出 JavaScript 安全整数范围的整数 | 十进制字符串 |
| DATE / DATETIME / TIMESTAMP | ISO 格式字符串，不擅自添加时区 |
| TIME | 可带负号、超过24小时、可选6位小数的 HH:MM:SS 字符串 |
| BINARY / VARBINARY / BLOB | `{"type":"binary","encoding":"base64","data":"..."}` |
| NULL | null |

查询默认最多 200 行，可通过 max_rows 设为 1–1000 且不超过配置上限。检测到额外一行时 truncated=true，结束本次连接，不继续排空余下结果。超大字段或结果返回明确容量错误；不会静默截断字段后伪装为完整数据。NaN/Infinity 和未支持类型不会产生非法 JSON。

## 限制与失败

总期限包含排队、进程启动、数据库连接、执行和传输。并发默认 2，最多 8；SQL/request/response/cell 的默认及硬上限分别为 65536/262144/1048576/65536 字节，用户可调低；输入行最低1024字节、输出行最低16384字节以容纳正常协议。期限默认 15 秒、最多 120 秒；返回行数默认 200、配置上限最多 1000。队列也有上限，超载时明确拒绝。

连接、读和写还有独立 socket 超时；这些超时不替代总期限。取消或超时只回收本次工作进程，不停止 MySQL 服务。服务日志只写 stderr，stdout 仅供 MCP。

字段和结果容量按 UTF-8 JSON 编码检查，二进制还包含 base64 编码开销；检查发生在驱动读出字段之后，不代表 MySQL 服务器或进程的硬内存配额。对大字段优先在 SQL 中选择必要列或限制长度。

错误包含受控 code/message；数据库错误可以包含数字 mysql_errno，不返回 SQL、数据值、配置原文或完整异常。变更如果尚未开始，outcome=not_started；已经发出而响应未知时，outcome=unknown。**unknown 不能当成回滚或失败后安全重试的证明。** 先从数据库核对实际结果，避免重复写入或重复 DDL。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 找不到 uvx | 使用绝对路径，或修复客户端 PATH |
| 缺少连接/配置 | 先执行 --check-config，核对 --connection 名称和配置路径 |
| 连接拒绝 | 核对 MySQL 服务、host/port 和网络；错误不代表 SQL 已执行 |
| 写入/DDL 模式无法启动 | 同时检查 allow_* 和 --mode；使用对应权限账号 |
| 生产变更被拒绝 | 检查 production 标记及 confirm_environment 参数 |
| SQL 被策略拒绝 | 检查支持范围和直接 schema 引用；不会自动改写成另一条危险 SQL |
| 超时/结果过大 | 缩小查询范围或列，核对索引；在硬上限内调整配置 |
