# 首版设计

## 目标与发布边界

将既有内部 MySQL MCP 整理为可独立安装、公开分发的 Python 项目。用户已确认独立目录、独立 Git、迁移测试、脱敏配置、修复发布缺口，并授权 GitHub 建仓和同步。首版为本地 stdio；使用者自己保管凭据、连接自己的数据库。远程 MCP、多租户和代管数据库不在首版范围内。

仓库名 `mysql-mcp`，Python 分发名 `mysql-mcp-router`，导入包 `mysql_mcp`，命令 `mysql-mcp`。首版 0.1.0，MIT。GitHub 提供源码和构建包，支持 `uvx --from git+https://github.com/hunxuankai/mysql-mcp@v0.1.0 mysql-mcp ...`。PyPI 名称已检查未占用，但 GitHub 发布不冒充 PyPI 上传；发布文档分别说明两条渠道。

Python >=3.11。运行依赖：MCP SDK >=2.3.0,<3、PyMySQL[rsa] >=1.2.3,<2、SQLGlot >=30.22.0,<31；三项直接依赖均为 MIT。依赖锁定到当前实际可安装版本，不扫描其他用户或工具的缓存。

## 模块与接口

- `config.py`：TOML 和 CLI 配置校验；生成只在内存中传递的数据库请求。
- `server.py`：CLI 和官方 SDK v2 的 Server、工具定义、协议错误与结果转换。
- `transport.py`：有界 UTF-8 stdio framing，交给 SDK 处理会话；无效消息返回受控 JSON-RPC 错误后继续；notification 不产生应答。
- `runner.py`：异步启动一次性 worker，标准输入传入凭据；并发、排队和总期限、取消及进程回收。
- `policy.py`：SQL 单语句、模式、危险功能和直接 schema 目标校验。
- `serialization.py`：数据库值的 JSON 编码以及结果容量限制。
- `database.py`：PyMySQL 连接、只读事务、流式读取和显式提交。
- `worker.py`：一次读取一个请求、执行一次操作、输出一个受控 JSON 结果后退出。

协议/配置代理和数据库代理以如下接口交接，不共享需要并行编辑的文件：

```python
# database.py
def execute_request(payload: dict[str, Any]) -> dict[str, Any]: ...

# runner.py
async def run_job(payload: dict[str, Any], *, timeout_seconds: float,
                  max_output_bytes: int) -> dict[str, Any]: ...
```

worker 输入包含：

```json
{
  "connection": {
    "host": "127.0.0.1", "port": 3306, "user": "fixture_reader",
    "password": "synthetic-test-only", "database": "mcp_fixture",
    "tls": {"enabled": false, "ca": null, "cert": null, "key": null},
    "connect_timeout": 5, "read_timeout": 5, "write_timeout": 5
  },
  "profile": "local", "production": false,
  "mode": "query", "operation": "query_sql", "allow_write": false, "allow_ddl": false,
  "arguments": {"sql": "SELECT 1", "max_rows": 200},
  "limits": {
    "max_rows": 1000, "max_sql_bytes": 65536, "max_request_bytes": 262144,
    "max_response_bytes": 1048576, "max_cell_bytes": 65536,
    "request_timeout_seconds": 15, "max_concurrency": 2
  }
}
```

worker 结果二选一：`{"ok":true,"data":{...}}` 或 `{"ok":false,"error":{"code":"...","message":"受控消息","mysql_errno":1064,"outcome":"not_started|unknown"}}`。不需要的错误字段省略。`execute_request` 把预期配置/SQL/数据库错误转为此格式，不泄露异常字符串；worker 最外层捕获非预期错误也只输出固定 `INTERNAL_ERROR`。parent 将其转换为 SDK `CallToolResult`，包含 `structured_content` 和兼容的 JSON 文本。最终 JSON-RPC 整行仍受响应预算限制。

## 配置契约

CLI 支持 `--config PATH`、`--connection NAME`（默认 local）、`--mode query|write|ddl`（默认 query）、`--check-config`、`--version`、`--help`，以及 `python -m mysql_mcp`。`--config` 可由 `MYSQL_MCP_CONFIG` 提供；不猜测仓库结构，不读取 Spring 配置。

TOML 使用 `[connections.local]`，字段为 host、port、database、user、password、production、allow_write、allow_ddl；前三项权限标记默认 false。`[connections.local.accounts.query]` / write / ddl 可覆盖 user、password，以支持分模式账号；也允许建立独立连接 profile。`[connections.local.tls]` 使用 enabled、ca、cert、key；开启时必须验证证书与主机名，不暴露关闭验证的选项。默认本机地址关闭 TLS，非本机地址默认开启，可通过显式 enabled=false 表示使用者确认其隔离网络条件。ca/cert/key 相对路径从配置文件目录解析；客户端证书和私钥必须成对。

`[limits]` 可调上述限制；最小值均正数，max_rows 上限 1000，SQL <=65536 bytes，request <=262144 bytes，response <=1048576 bytes，cell <=65536 bytes，期限 <=120 秒，并发 <=8；默认如请求示例。用户可以调低容量；单元测试拒绝 bool 充当 int、NaN/Infinity、未知字段、缺失凭据、非法端口和不成对证书。不在错误或 check-config 输出真实密码、配置内容或秘密路径。密码允许空字符串以兼容明确配置的本机实例，但缺字段拒绝。

## SQL 与权限

query 提供 list_tables、describe_table、show_create_table、query_sql；write 提供 write_sql；ddl 提供 ddl_sql。保持工具名称，模式只暴露自身工具。未配置 allow_write/allow_ddl 时相应模式启动失败；production=true 的变更还要求工具参数 `confirm_environment="production"`。该字段仅防误用，不作为身份认证。

只允许单语句，尾部分号和普通注释可用；语句中的分号字符串正常。拒绝执行型注释、危险 hints、多语句、文件读写、显式锁、睡眠/基准测试函数、CALL、用户/角色/数据库管理、授权、触发器/事件/存储程序。query 使用 MySQL READ ONLY 事务，并加 SELECT 执行预算；write 允许 INSERT/UPDATE/DELETE/REPLACE，包括合法 CTE；ddl 只开放表和索引的 CREATE/ALTER/DROP、TRUNCATE TABLE、简单 RENAME TABLE，不开放全服务器管理语句。

SQLGlot 解析严格使用 MySQL 方言，不将未知 Command 当白名单语句。对直接表目标（含子查询、CTE、外键目标）拒绝其他 schema 或 catalog。可查询受控的 SHOW TABLES/COLUMNS/INDEX/CREATE TABLE、DESCRIBE 和 EXPLAIN SELECT；不要为了支持一个 SHOW 分支放开整个全局 SHOW。工具 table 参数是当前库中的单个表名，正确转义 Unicode、空格和反引号，不把输入拼成任意 SQL。

校验与执行的引号语义必须一致：连接初始化将 SESSION sql_mode 的 ANSI_QUOTES、NO_BACKSLASH_ESCAPES 去掉，保留其余模式，避免词法解释分歧。数据库账号按具体库授予最小权限；SQL 检查是附加防护，不承诺隔离数据库管理员安装的 UDF、触发器或 SQL SECURITY DEFINER 程序。文档必须说明这条权限边界。

## 资源与错误

请求总期限从进入执行排队开始计算，覆盖等待、启动、认证、执行和传输；超时/取消只终止本次创建的 worker，并等待回收。Windows 子进程无可见窗口。并发默认 2，排队请求也受期限约束。不要启动无限量排队任务；stdio 请求同时等待数应有界。

所有模式有连接/读/写 socket timeout。查询使用 SSCursor，只取 max_rows+1 判断截断，关闭连接而非排空无限结果；避免调用会自动排空的 SSCursor.close。每个 worker 只执行一次操作，完成即释放全部状态。每个 cell、结果和协议行都有字节限制，超限明确返回容量错误，不把截掉的字段伪装成完整数据。

Decimal 为精确字符串；超出 JavaScript 安全整数范围的整数为字符串；日期/时间戳为 ISO 字符串；MySQL TIME 为可包含负号、超过 24 小时、可选 6 位小数的 HH:MM:SS 字符串；二进制为 `{"type":"binary","encoding":"base64","data":"..."}`。禁止 NaN/Infinity JSON，其他未知类型明确报错。rows 为二维数组，columns 为数组，保留重复列名；返回 row_count、truncated。

错误仅包含受控 code/message 和可选 MySQL 数字 errno；不回显服务端异常、SQL、值、路径或凭据。变更发送后超时、取消、断线或提交响应丢失只能报告 outcome=unknown；不自动重试。DDL 隐式提交，不宣传可回滚。日志仅 stderr，使用操作名、模式、状态、耗时等字段，不输出请求/响应正文。

## 验收与交付

- 单元与回归：CTE、注释、边界、跨 schema、管理型 DDL、生产确认、不同字段类型、大小/超时/取消、异常请求后的正常 ping。
- 官方 SDK 客户端端到端握手、工具发现、调用、错误、取消；坏 JSON、数组/null、非法参数和超长行不得导致服务退出。
- 一次性 MySQL 库真实验证 query/write/ddl、精确小数、TIME/二进制、截断后续查询、只读保护与超时；使用唯一随机数据库名，只清理本次创建的库。CI 提供 MySQL 8.0/8.4 服务，明确仅测试这些版本。
- ruff、mypy、pytest、wheel/sdist 构建和 twine 元数据校验；从 wheel 及 GitHub 版本安装后验证帮助、配置、协议。
- CI 覆盖 Windows/macOS/Linux 和支持 Python 版本；未在本机执行的平台以 CI 的实际状态报告。
- README、配置/客户端示例、最小 GRANT 示例、错误与限制、升级、贡献/安全报告、CHANGELOG、依赖许可证和发布步骤。
- 仓库、构建包、Git 历史中没有源项目真实配置；发布前进行凭据及文件清单检查。
- GitHub 建公开仓库、推送经过验证的 main、发布 v0.1.0 及 wheel/sdist/checksums；PyPI 上传需发布者账号配置，未实际上传时不得提供一个假的已上架结论。
