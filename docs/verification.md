# 首版本地验收

2026-10-10，Windows、Python 3.11.13、MySQL 8.4.10。

| 检查 | 实际结果 |
| --- | --- |
| 单元与协议 | 126 项通过 |
| 一次性真实 MySQL | 13 项通过 |
| ruff / 格式 / mypy | 全部通过，11 个源码文件 |
| wheel / sdist / twine | 构建和元数据验证通过 |
| 发行文件检查 | 无本机配置或危险归档路径，版本与 tag 一致 |
| 干净虚拟环境安装 | 使用 uv.lock 中的缓存依赖和构建 wheel，32 个运行包依赖兼容 |
| 安装后验证 | 在独立工作目录导入安装包、CLI、配置检查、官方 SDK 握手/发现/拒绝SQL/后续ping通过 |
| 数据清理 | 精确创建并删除本次唯一临时库及3个最小权限账号，库/账号残留为0 |
| 独立审查 | 取消配额泄漏和非法 Unicode ID 问题均修复并通过复核 |

直接在线安装一度遇到本机 PyPI TLS 握手中断；隔离环境的依赖按已验证锁文件安装，没有关闭 TLS 校验。测试未修改任何业务库，已有数据库服务保持运行。

GitHub Actions 另行执行 Windows/macOS/Linux × Python 3.11–3.14 及 MySQL 8.0/8.4；实际结果见 [CI](https://github.com/hunxuankai/mysql-mcp/actions/workflows/ci.yml)。本地验证不冒充其他平台已执行。
