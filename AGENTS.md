# MySQL MCP 开发规则

- 文档和提交描述默认中文，代码、标识符使用英文。
- 本仓库是独立 Python 包，源码位于 `src/mysql_mcp/`；最低 Python 3.11。
- 项目对外发布：禁止提交实际连接配置、密码、令牌、数据库内容、日志或个人路径。示例和测试凭据必须仅属于隔离测试。
- 使用官方 MCP SDK、PyMySQL 和 SQLGlot；版本范围在 `pyproject.toml`，可复现开发依赖在 `uv.lock`。
- 默认只读。写入和 DDL 必须配置开启；生产变更还要求显式生产确认。SQL 策略是附加防护，数据库最小授权是权限边界。
- SQL、参数、连接凭据和完整异常不得写入日志。错误使用受控代码和脱敏消息。
- 协议、配置、SQL 语义或超时行为变更必须有针对性测试，更新 README 或对应文档；缺陷先写能失败的回归测试。
- 工作进程仅处理一次数据库操作，凭据经标准输入传递，不进入命令行或临时文件；超时仅结束自己创建的子进程。
- 执行 `uv run pytest`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy src`、`uv build` 和 `uv run twine check dist/*`。真实 MySQL 测试只能使用一次性隔离库，禁止连接业务库写入。
- 多代理按文件分工，不回退他人修改，不自行派生代理。提交、GitHub 同步及最终验收由主代理统一处理。

