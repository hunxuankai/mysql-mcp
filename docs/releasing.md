# 发布流程

## GitHub 首版渠道

1. 确认 CHANGELOG、pyproject.toml 和 `mysql_mcp.__version__` 的版本一致；更新示例中的固定 tag。
2. 运行开发文档的测试/类型/lint/构建、发行文件检查和干净环境 wheel 安装验证。
3. 检查变更清单和历史：实际数据库配置、令牌、私钥、运行日志和本机私人路径不得进入仓库或产物。
4. 推送 main，等待 CI 的系统/Python 和 MySQL 8.0/8.4 验证全部完成。
5. 对通过验证的提交创建 `v<版本>` tag 并推送。Release workflow 会重新验证，核对 tag/version、构建 wheel/sdist、生成 SHA256SUMS，并创建 GitHub Release。
6. 回读 Release 的资产清单，验证固定 tag 的 uvx 安装和实际命令。记录结果，不能把工作流排队当作发布成功。

Actions 固定到已核对的 commit。CI 只有 contents:read；仅 release 上传任务有 contents:write。发布脚本不接受未匹配项目版本的 tag。

## PyPI 渠道

GitHub 仓库、Release 和 PyPI 是不同渠道。首版通过 GitHub 固定 tag/wheel 即可安装；未上传 PyPI 时不要宣传 `uvx mysql-mcp-router` 已可用。

需要 PyPI 时，维护者先在自己的 PyPI 账号创建项目并配置 Trusted Publisher，或使用受保护的发布令牌。核对 `mysql-mcp-router` 名称仍可用，再发布同一批经过验证的 wheel/sdist。令牌只能保存在发布者的凭据系统中，不写仓库、文档或命令历史。

本仓库没有默认启用向 PyPI 自动上传的工作流，避免 GitHub tag 被误认为已获 PyPI 发布身份。上传后应从 PyPI 再次安装并验证，然后更新 README 的渠道说明。

## 升级

使用者把客户端固定版本改为目标 tag，先执行 --check-config 完成依赖预热，再重启对应 MCP 进程。变更 SQL 策略、类型编码、配置字段或默认限制时，在 CHANGELOG 中说明迁移行为。
