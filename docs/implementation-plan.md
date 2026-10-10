# 独立 MySQL MCP Implementation Plan

> 状态：2026-10-11 已完成。实施由主代理接管并经过独立审查；完整验收和发布渠道见 [verification.md](verification.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Ownership boundaries permit Tasks 1 and 2 to execute concurrently after the foundation commit. 主代理协调验证和提交，不派生第二层代理。

**Goal:** 交付可独立安装、默认只读、执行有界的 MySQL MCP，并建立公开 GitHub 仓库。

**Architecture:** 官方 MCP SDK v2 负责会话和工具协议，自定义 framing 限制消息大小。每个调用经标准输入交给独立 PyMySQL worker，SQLGlot 校验模式与直接库目标，parent 控制并发和总期限。

**Tech Stack:** Python >=3.11、MCP SDK >=2.3.0,<3、PyMySQL[rsa] >=1.2.3,<2、SQLGlot >=30.22.0,<31、pytest、ruff、mypy、uv、GitHub Actions。

**Spec:** [design.md](design.md)。设计契约优先于本文；字节限制与 worker JSON 接口在设计中定义，实施不得自创不同字段。

## Global Constraints

- Python >=3.11；命令 mysql-mcp，包 mysql-mcp-router 0.1.0，导入 mysql_mcp。
- 只支持本地 stdio；默认 query，write/ddl 需配置开启，生产变更需确认。
- max_rows<=1000、SQL<=65536 bytes、request<=262144 bytes、response<=1048576 bytes、cell<=65536 bytes、总期限默认15且<=120秒、并发默认2且<=8。
- 凭据只在本机配置和内存，worker 经 stdin 接收；日志/错误不得包含秘密、原始SQL或完整异常。
- 数据库账号的最小授权为权限边界，SQL 策略检查直接库目标；不承诺隔离管理员定义的副作用代码。
- 新增代码 strict mypy，公共函数有注解；错误行为必须先写失败测试。

## Task 1: SQL 策略、结果编码与数据库 worker

**Files:** Create src/mysql_mcp/policy.py, serialization.py, database.py, worker.py; tests/test_policy.py, test_serialization.py, test_database.py, test_worker.py。

**Interfaces:** Consumes design.md 中的 worker JSON payload；produces `execute_request(payload: dict[str, Any]) -> dict[str, Any]` 和 `python -m mysql_mcp.worker`。SQL 校验函数与序列化函数可自行命名，公共入口稳定。

- [x] 先建立回归测试：旧实现的 bytes/timedelta JSON 失败、跨 schema 和管理 DDL 的放行，以及读取max_rows后仍排空的问题。对新接口写断言，例如 `execute_request(payload)["error"]["code"]` 为受控错误，且连接桩未调用；针对缺少模块的导入失败跑一轮，随后以实现前的行为缺口作为红灯。
- [x] 实现严格单语句 MySQL 策略。参数化覆盖 SELECT/递归CTE/多CTE/嵌套CTE、字符串分号、普通注释、REPLACE函数；拒绝多语句、写CTE走query、执行注释、文件函数/输出、锁/睡眠、跨 schema 子查询和外键、用户/数据库/存储程序管理。EXPLAIN仅包裹已验证读语句。
- [x] 编码测试断言 `Decimal('0.100') -> '0.100'`、`timedelta(hours=27) -> '27:00:00'`、负微秒准确、bytes('AB')->base64 'QUI='、大整数保真、NaN拒绝和超大cell拒绝；实现对应编码与容量计算。
- [x] 实现 PyMySQL 每调用新连接，禁多语句/local infile，所有模式 socket 时限，sql_mode 一致性；query READ ONLY，SSCursor最多 fetch max_rows+1 后关闭连接，不排空。写入commit，DDL显式标注隐式提交边界。
- [x] 生产确认和 allow_write/allow_ddl 在 worker 中再次检查；合法 mutation 参数拒绝后无连接调用。异常只给可控code、errno、outcome，不回显异常值。测试放入合成秘密哨兵验证不泄漏。
- [x] worker从stdin读一个有界JSON请求、输出一个JSON结果；非法输入/意外错误不出traceback，stdout纯协议，连接结束；测试以subprocess跑无连接路径。
- [x] 运行负责的 pytest、ruff 和 mypy；在 .local/task-1-report.md 记录红绿证据、已测命令、风险。主代理负责提交。

## Task 2: 配置、SDK 服务和有界执行器

**Files:** Create src/mysql_mcp/config.py, server.py, transport.py, runner.py, __main__.py; tests/test_config.py, test_runner.py, test_protocol.py。

**Interfaces:** Consumes worker JSON contract 和 `python -m mysql_mcp.worker`；produces CLI `mysql_mcp.server:main()`、配置到payload转换、`run_job(payload, *, timeout_seconds, max_output_bytes)`，返回worker result。可使用无数据库的受控子进程夹具测试runner，不依赖Task1源码完成。

- [x] 先写失败测试：默认query；config绝对/相对路径；未知键、bool端口、无穷期限、配置解析错误不包含值；生产权限检查；分模式账号覆盖；证书验证和成对配置。
- [x] 按design实现TOML配置、CLI、version/help/check-config；禁止密码出现在repr/check-config/errors。命令工作目录与包安装位置无关。
- [x] 用实际mcp2.3源码核对Server v2 API，使用显式input_schema和additionalProperties:false；工具参数依schema校验，bool不是max_rows。query暴露4工具，write/ddl各1；production工具schema包含确认const。
- [x] 先复现坏消息测试，再实现有界UTF-8 stdio：bad JSON、[]、null、非法id/params、无id notification、超长行之后均可ping；不在stdout打印日志。保持SDK协议协商，不手写固定协议版本。
- [x] runner使用异步子进程communicate和总deadline；队列和启动也在预算内，限并发/队列，超时和取消kill仅自己的进程并await回收。测试sleep夹具、stderr/超大输出、启动错误、取消、排队到期、变更unknown outcome。Windows CREATE_NO_WINDOW。
- [x] 用官方ClientSession从实际子进程测试初始化/工具发现/拒绝调用与后续ping；不要求业务库。返回标准CallToolResult，最终响应也限字节且敏感错误脱敏。
- [x] 执行负责测试、ruff、mypy；在.local/task-2-report.md记录红绿证据与已测命令。主代理负责提交。

## Task 3: 打包、文档、跨平台与真实验证

**Files:** pyproject.toml, uv.lock, README.md, examples/, docs/usage.md, docs/development.md, docs/releasing.md, CHANGELOG.md, SECURITY.md, .github/workflows/ci.yml, .github/workflows/release.yml, tests/integration/。

**Interfaces:** 消费 CLI、config 和worker契约，不修改Tasks1/2负责模块，除非代理交接或审查修复明确转交。

- [x] 写README与配置、主流客户端JSON/TOML示例、三个最小权限GRANT示例；示例凭据为明确占位符，不引用原项目配置。
- [x] 写安装/升级、TLS、SQL支持、字段编码、限额、变更结果未知、DDL提交和问题排查说明；MIT和依赖许可记录；GitHub与PyPI发布流程分别说明。
- [x] CI OS/Python矩阵跑pytest/lint/typecheck/build；Linux MySQL8.0/8.4服务跑隔离集成测试。Release工作流先验证再构建上传wheel/sdist/checksums，最小权限。
- [x] 在本机建立一次性MySQL库并记录准确随机名；仅此库执行query/write/ddl/类型/截断/超时用例，最终精确删除。数据库由已有工具正常启动/停止，恢复原服务状态；不能修改业务数据。
- [x] 从构建wheel在干净环境安装并验证CLI、官方SDK协议；检查sdist/wheel文件清单无凭据、.git、.local等。扫描新仓库与历史只报告路径/计数，不打印秘密。
- [x] 使用独立审查代理检查最终实现与spec，修复后复验受影响用例；完成全部验证才提交、建立GitHub公开仓库、推main和v0.1.0、上传Release并核对远端。

## Task 4: 原使用方迁移与收尾

**Files:** 原项目的.codex/config.toml、.codex/mysql-connections.toml、MCP旧脚本/测试、环境文档、Trellis运行规范与任务记录。

**Interfaces:** 原使用方通过明确路径和`uv run --frozen --project <独立项目>`启动新CLI；连接值保持不变，只调整配置结构，绝不复制到公开仓库。

- [x] 按新config契约转换原连接文件的键，逐字段核对实际值保持；重新指向包CLI，保留原工具分模式配置。
- [x] 删除旧实现和已迁移测试，避免双份维护；更新文档指向独立仓库，调整已过时资源限制/测试事实。
- [x] 以同样启动命令单独验证配置、stdio握手/工具发现。现有会话进程不冒充已热重载，下次会话加载新配置。
- [x] 主代理提交原项目的关联迁移与Trellis记录；不推送原私有项目、不改变其仓库可见性。归档任务，最终交付路径、GitHub链接、实际测试结果与渠道限制。
