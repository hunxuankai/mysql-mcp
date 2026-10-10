-- 仅为授权示例：由自己的数据库管理员替换库名、主机和密码后执行。
-- 账号 host 必须匹配实际 MCP 客户端所在位置；这里示范本机。
-- 不授予全局 *.*、FILE、SUPER、CREATE USER、GRANT OPTION 等权限。

CREATE USER 'mcp_reader'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, SHOW VIEW ON `app_database`.* TO 'mcp_reader'@'localhost';

CREATE USER 'mcp_writer'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, INSERT, UPDATE, DELETE ON `app_database`.* TO 'mcp_writer'@'localhost';

CREATE USER 'mcp_schema'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT CREATE, ALTER, DROP, INDEX, REFERENCES ON `app_database`.* TO 'mcp_schema'@'localhost';

-- 不需要的写入/DDL账号无需创建。
