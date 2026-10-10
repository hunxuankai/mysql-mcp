# 第三方依赖

本项目源码使用 MIT。依赖通过 Python 包管理器安装，没有把以下项目的源码复制进本包。

| 直接运行依赖 | 首版验证版本 | 许可证 | 项目 |
| --- | --- | --- | --- |
| MCP Python SDK | 2.3.0 | MIT | https://github.com/modelcontextprotocol/python-sdk |
| PyMySQL | 1.2.3 | MIT | https://github.com/PyMySQL/PyMySQL |
| SQLGlot | 30.22.0 | MIT | https://github.com/tobymao/sqlglot |
| AnyIO | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema |

PyMySQL 的 rsa extra 用于 MySQL 的 SHA-2 认证支持，会安装 cryptography；其许可证为 Apache-2.0 或 BSD-3-Clause。其余传递依赖各自保留发行包中的许可证；具体解析版本见 uv.lock。发布时应保留项目 LICENSE，重新分发依赖或制作完整离线包时另行保留相应依赖许可证。
