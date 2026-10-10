# 开发与验证

## 本地环境

```shell
uv sync --locked --python 3.11
uv run pytest -m "not integration"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
uv run twine check dist/*
uv run python scripts/check_distribution.py dist
```

协议测试会启动本项目的 stdio 子进程和受控测试 worker，不需要数据库。测试使用临时目录和合成凭据，不读取使用者的默认连接配置。

## 真实 MySQL 测试

仅在明确配置 `MYSQL_MCP_INTEGRATION=1` 后启用。需要一个允许创建/删除临时测试库的专用测试实例或测试管理员；不要指向生产实例。测试创建随机 `mcp_test_<uuid>` 库，记录准确名称，只清理本次创建的库，不扫描和删除已有库。

环境变量：MYSQL_MCP_TEST_HOST（默认127.0.0.1）、MYSQL_MCP_TEST_PORT（默认3306）、MYSQL_MCP_TEST_USER、MYSQL_MCP_TEST_PASSWORD。也可通过 MYSQL_MCP_TEST_DEFAULTS_FILE 指定使用者自己的本机 MySQL 客户端配置，仅供测试进程读取；文件不进入仓库或产物。

PowerShell 示例（所有值仅为本机隔离测试实例的示例）：

```powershell
$env:MYSQL_MCP_INTEGRATION = '1'
$env:MYSQL_MCP_TEST_HOST = '127.0.0.1'
$env:MYSQL_MCP_TEST_PORT = '3306'
$env:MYSQL_MCP_TEST_USER = 'test_admin'
$env:MYSQL_MCP_TEST_PASSWORD = 'YOUR_TEST_INSTANCE_PASSWORD'
uv run pytest -m integration -v
```

测试覆盖读写/DDL、CTE、精确数值、TIME/二进制、截断和后续操作、策略拒绝、容量和变更超时。测试退出会清理准确归属的资源；若测试进程被外部强杀，先核对 `.local/integration-schema.json` 中本次唯一库名，再由测试管理员检查，不能按前缀批量删除。

GitHub Actions 使用一次性 MySQL 8.0/8.4 服务，公开的服务密码只属于该临时 CI 容器。Windows/macOS/Linux 和 Python 3.11–3.14 的协议与单元测试独立运行。

## 贡献

先描述需要改变的实际行为和兼容性，再给出最小改动。SQL、协议、配置、序列化、进程生命周期的修复应先写失败测试。错误消息和测试失败信息也不能泄露真实凭据。遵循 AGENTS.md 的文件分工与检查要求。
