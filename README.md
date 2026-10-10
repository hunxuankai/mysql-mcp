# MySQL MCP Router

[![CI](https://github.com/hunxuankai/mysql-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/hunxuankai/mysql-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

让支持 MCP 的客户端通过本地 stdio 查询和管理自己的 MySQL。默认只读；写入和 DDL 分开启用，生产变更需要额外确认。每次操作由独立工作进程执行，限制运行时间和返回数据量。

Python **3.11–3.14**；Windows、macOS、Linux；目标数据库 **MySQL 8.0 / 8.4**。MariaDB 和其他 MySQL 兼容数据库尚未纳入兼容性承诺。

## 快速开始

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Git。下载 [配置示例](examples/config.example.toml)，另存到仓库外，例如 `C:/Users/your-name/.config/mysql-mcp/config.toml`，填写自己数据库的地址和只读账号。

预下载固定版本并检查配置：

```shell
uvx --python 3.11 --from git+https://github.com/hunxuankai/mysql-mcp@v0.1.0 mysql-mcp --config C:/Users/your-name/.config/mysql-mcp/config.toml --connection local --check-config
```

macOS/Linux 将配置路径替换为自己的绝对路径。`--check-config` 只检查本机配置，不连接数据库、不输出密码。首次运行需要下载依赖；检查通过后在 MCP 客户端中添加：

```json
{
  "mcpServers": {
    "mysql-query": {
      "command": "uvx",
      "args": [
        "--python", "3.11",
        "--from", "git+https://github.com/hunxuankai/mysql-mcp@v0.1.0",
        "mysql-mcp",
        "--config", "C:/Users/your-name/.config/mysql-mcp/config.toml",
        "--connection", "local"
      ]
    }
  }
}
```

客户端进程必须能找到 `uvx`；图形客户端未继承 PATH 时，将 command 换成 uvx 的绝对路径。上面的 `mcpServers` 示例适用于采用该配置结构的客户端。Codex 使用 TOML，见 [客户端示例](examples/codex.toml) 和 [详细接入](docs/usage.md)。

也可下载 [GitHub Release](https://github.com/hunxuankai/mysql-mcp/releases) 的 wheel，并使用 `uvx --from /absolute/path/mysql_mcp_router-0.1.0-py3-none-any.whl mysql-mcp ...`。当前安装说明使用 GitHub 固定版本；包名 `mysql-mcp-router` 不表示已经上传 PyPI。

## 工具与模式

| 模式 | 工具 | 开启条件 |
| --- | --- | --- |
| query（默认） | list_tables、describe_table、show_create_table、query_sql | 配置数据库连接，使用只读账号 |
| write | write_sql | `allow_write=true`，启动增加 `--mode write` |
| ddl | ddl_sql | `allow_ddl=true`，启动增加 `--mode ddl` |

当连接标记为 `production=true` 时，write_sql/ddl_sql 还必须传 `confirm_environment="production"`。此参数用于防止误选环境；真正的权限由使用者的数据库账号和 MCP 客户端控制。

query_sql 默认返回最多 200 行，允许 1–1000 行。支持 SELECT、CTE 和受控元数据查询；写入支持 INSERT/UPDATE/DELETE/REPLACE，DDL 只开放表和索引操作。完整支持范围、数据编码和错误语义见 [使用说明](docs/usage.md)。每次调用使用新连接，不提供跨调用事务、临时表或会话变量。

## 执行限制

| 限制 | 默认值 |
| --- | --- |
| 单次操作总期限（含排队） | 15 秒 |
| 同时执行的数据库操作 | 2 |
| SQL 文本 | 64 KiB |
| 输入协议行 | 256 KiB |
| 输出协议行 | 1 MiB |
| 单个字段 | 64 KiB |
| 查询返回行数 | 默认 200，上限 1000 |

限制可在 `[limits]` 中调整，范围见 [配置示例](examples/config.example.toml)。超时或取消时仅回收本次工作进程。**变更发送后发生超时或断线，结果可能未知，不应自动重试。** DDL 可能隐式提交。

## 权限与配置

实际配置放在本机、不要提交 Git。优先使用 [最小权限 SQL 示例](examples/grants.sql) 中的独立账号；可以给三个模式分别配置账号。远程数据库默认启用 TLS 并验证主机名，支持自定义 CA 和客户端证书。数据库管理员定义的触发器、UDF 和 SQL SECURITY DEFINER 程序不属于 SQL 文本检查的隔离承诺，详见 [安全说明](SECURITY.md)。

## 开发与发布

```shell
git clone https://github.com/hunxuankai/mysql-mcp.git
cd mysql-mcp
uv sync --locked --python 3.11
uv run mysql-mcp --help
uv run pytest -m "not integration"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
uv run twine check dist/*
```

[开发与隔离测试](docs/development.md) · [发布流程](docs/releasing.md) · [更新记录](CHANGELOG.md) · [设计](docs/design.md)

源码为 [MIT](LICENSE)。依赖由包管理器独立安装，直接依赖许可证见 [第三方说明](THIRD_PARTY_NOTICES.md)。
