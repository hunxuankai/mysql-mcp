from __future__ import annotations

import pytest

from mysql_mcp.policy import PolicyError, quote_identifier, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT 'a;b'",
        "SELECT '/*! still text */'",
        "SELECT 'text --" + chr(160) + " is a literal'",
        "SELECT 1; -- normal comment\n",
        "SELECT REPLACE('a', 'a', 'b')",
        "WITH q AS (SELECT 1) SELECT * FROM q",
        "WITH RECURSIVE q(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM q WHERE n<3) SELECT * FROM q",
        "WITH q AS (SELECT 1), r AS (SELECT * FROM q) SELECT * FROM r",
        "WITH q AS (WITH r AS (SELECT 1) SELECT * FROM r) SELECT * FROM q",
        "SHOW TABLES",
        "SHOW FULL TABLES FROM fixture",
        "SHOW COLUMNS FROM fixture.example",
        "SHOW INDEX FROM example",
        "SHOW CREATE TABLE example",
        "DESCRIBE example",
        "EXPLAIN SELECT 1",
        "EXPLAIN FORMAT=JSON SELECT 1",
    ],
)
def test_read_queries(sql: str) -> None:
    assert validate_sql(sql, "query", "fixture") == sql.strip()


@pytest.mark.parametrize(
    "mode,sql",
    [
        ("query", "SELECT 1; DELETE FROM example"),
        ("query", "WITH q AS (SELECT 1) DELETE FROM example"),
        ("query", "WITH q AS (DELETE FROM example) SELECT * FROM q"),
        ("query", "SELECT 1 INTO OUTFILE '/no-file'"),
        ("query", "SELECT 1 /*! INTO OUTFILE '/no-file' */"),
        ("query", "SELECT /*+ MAX_EXECUTION_TIME(0) */ 1"),
        ("query", "SELECT GET_LOCK('lock', 1)"),
        ("query", "SELECT LOAD_FILE('/no-file')"),
        ("query", "SELECT SLEEP(2)"),
        ("query", "SELECT * FROM example FOR UPDATE"),
        ("query", "SELECT 1 --" + chr(160) + "suffix INTO OUTFILE '/never-created'"),
        ("query", "EXPLAIN ANALYZE SELECT 1"),
        ("query", "SELECT * FROM other.example"),
        ("query", "SELECT * FROM fixture.example WHERE id IN (SELECT id FROM other.example)"),
        ("query", "SHOW DATABASES"),
        ("query", "SHOW TABLES FROM other"),
        ("query", "SELECT other.func(1)"),
        ("write", "DELETE FROM other.example"),
        ("ddl", "DROP DATABASE fixture"),
        ("ddl", "ALTER USER sample ACCOUNT LOCK"),
        ("ddl", "CREATE USER sample"),
        ("ddl", "CREATE TRIGGER bad BEFORE INSERT ON t FOR EACH ROW SET @a=1"),
        ("ddl", "CREATE TABLE t (id INT REFERENCES other.t(id))"),
        ("ddl", "CREATE TABLE t LIKE other.t"),
        ("ddl", "ALTER TABLE t RENAME TO other.t"),
        ("ddl", "RENAME TABLE t TO other.t"),
        ("ddl", "CREATE TABLE t (id INT) ENGINE=FEDERATED CONNECTION='mysql://no'"),
    ],
)
def test_forbidden_sql(mode: str, sql: str) -> None:
    with pytest.raises(PolicyError):
        validate_sql(sql, mode, "fixture")


@pytest.mark.parametrize(
    "mode,sql",
    [
        ("write", "INSERT INTO t VALUES (1)"),
        ("write", "REPLACE INTO t VALUES (1)"),
        ("write", "UPDATE t SET a=1"),
        ("write", "DELETE FROM t"),
        ("write", "WITH q AS (SELECT 1 AS id) UPDATE t JOIN q ON t.id=q.id SET a=1"),
        ("ddl", "CREATE TABLE t (id INT)"),
        ("ddl", "ALTER TABLE t ADD COLUMN a INT"),
        ("ddl", "DROP TABLE t"),
        ("ddl", "TRUNCATE TABLE t"),
        ("ddl", "CREATE INDEX ix ON t (id)"),
        ("ddl", "DROP INDEX ix ON t"),
        ("ddl", "RENAME TABLE t TO renamed"),
    ],
)
def test_explicit_mutation_modes(mode: str, sql: str) -> None:
    assert validate_sql(sql, mode, "fixture") == sql


def test_table_name_is_one_quoted_identifier() -> None:
    assert quote_identifier("表 ` 名") == "`表 `` 名`"
    assert quote_identifier("other.table") == "`other.table`"
    with pytest.raises(PolicyError):
        quote_identifier("bad\x00name")


def test_parser_errors_do_not_log_sql(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(PolicyError):
        validate_sql("CREATE USER SECRET_SENTINEL", "ddl", "fixture")
    assert "SECRET_SENTINEL" not in caplog.text
