"""Conservative MySQL statement validation; database grants remain authoritative."""

from __future__ import annotations

import logging
from typing import ClassVar

import sqlglot
from sqlglot import exp
from sqlglot.dialects.mysql import MySQL
from sqlglot.errors import SqlglotError
from sqlglot.tokenizer_core import TokenType

# SQLGlot's fallback warnings contain the original SQL. This worker owns its logger.
logging.getLogger("sqlglot").disabled = True

UNSAFE_FUNCTIONS = {
    "LOAD_FILE",
    "GET_LOCK",
    "RELEASE_LOCK",
    "RELEASE_ALL_LOCKS",
    "IS_FREE_LOCK",
    "IS_USED_LOCK",
    "SLEEP",
    "BENCHMARK",
    "MASTER_POS_WAIT",
    "SOURCE_POS_WAIT",
    "WAIT_FOR_EXECUTED_GTID_SET",
}
READ_SHOW = {"TABLES", "COLUMNS", "INDEX", "INDEXES", "KEYS", "CREATE TABLE"}


class _StatementTokenizer(MySQL.Tokenizer):
    # Do not collapse unsupported command bodies into one STRING token: semicolons
    # and the identifiers of the narrowly supported RENAME syntax must remain visible.
    COMMANDS: ClassVar[set[TokenType]] = set()


class PolicyError(ValueError):
    """A statement or identifier is outside the supported SQL contract."""


def quote_identifier(value: str) -> str:
    if not value or len(value) > 64 or "\x00" in value:
        raise PolicyError("A table name must contain 1 to 64 non-NUL characters.")
    return "`" + value.replace("`", "``") + "`"


def _guard_comment_spacing(sql: str) -> None:
    """Do not let Unicode whitespace broaden MySQL's ASCII '--' comment rule."""
    index = 0
    quote: str | None = None
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            if char == "\\":
                if quote == "`":
                    raise PolicyError("Backslash-escaped identifiers are not supported.")
                index += 2
                continue
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise PolicyError("Unterminated SQL comment.")
            index = end + 2
            continue
        elif char == "#" or sql.startswith("--", index):
            comment = char == "#"
            if not comment:
                following = sql[index + 2 : index + 3]
                if following and ord(following) > 127:
                    raise PolicyError("Use ASCII whitespace after a double-dash comment marker.")
                comment = not following or ord(following) <= 32
            if comment:
                while index < len(sql) and sql[index] not in "\r\n":
                    index += 1
                continue
        index += 1


def _check_tree(tree: exp.Expr, database: str) -> None:
    for node in tree.walk():
        if isinstance(node, (exp.Command, exp.Into, exp.Lock, exp.Hint)):
            raise PolicyError("SQL contains an unsupported operation.")
        if isinstance(node, (exp.Table, exp.Column)) and (
            node.catalog or (node.db and node.db != database)
        ):
            raise PolicyError("Direct references must use the configured database.")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise PolicyError("Qualified stored function calls are not supported.")
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name in UNSAFE_FUNCTIONS:
                raise PolicyError("SQL contains a disallowed function.")
        if isinstance(node, exp.EngineProperty) and node.name.upper() == "FEDERATED":
            raise PolicyError("Federated tables are not supported.")
        if isinstance(node, exp.Property):
            rendered = node.sql(dialect="mysql").upper()
            if any(
                term in rendered for term in ("CONNECTION=", "DATA DIRECTORY", "INDEX DIRECTORY")
            ):
                raise PolicyError("External table storage is not supported.")


def _is_read(tree: exp.Expr) -> bool:
    if isinstance(tree, exp.Query):
        return not any(isinstance(node, (exp.DML, exp.DDL)) for node in tree.walk())
    if isinstance(tree, exp.Describe):
        inner = tree.this
        return isinstance(inner, exp.Table) or (isinstance(inner, exp.Expr) and _is_read(inner))
    return isinstance(tree, exp.Show) and str(tree.this).upper() in READ_SHOW


def validate_sql(sql: str, mode: str, database: str, max_bytes: int = 65536) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise PolicyError("A non-empty SQL statement is required.")
    try:
        if "\x00" in sql:
            raise PolicyError("NUL characters are not supported in SQL text.")
        if len(sql.encode("utf-8")) > max_bytes:
            raise PolicyError("SQL exceeds the configured byte limit.")
        _guard_comment_spacing(sql)
        tokens = _StatementTokenizer().tokenize(sql)
        for token in tokens:
            for comment in token.comments:
                if comment.lstrip().startswith(("!", "+", "M!")):
                    raise PolicyError("Executable comments and optimizer hints are not supported.")
        if tokens and tokens[-1].token_type == TokenType.SEMICOLON:
            tokens.pop()
        if not tokens or any(token.token_type == TokenType.SEMICOLON for token in tokens):
            raise PolicyError("Exactly one SQL statement is required.")
        source = sql[tokens[0].start : tokens[-1].end + 1]
        starter = tokens[0].text.upper()
        if (
            starter in {"EXPLAIN", "DESCRIBE", "DESC"}
            and len(tokens) > 1
            and tokens[1].text.upper() == "ANALYZE"
            and tokens[1].token_type != TokenType.IDENTIFIER
        ):
            raise PolicyError("EXPLAIN ANALYZE is not supported.")
        if starter == "REPLACE":
            # Parse REPLACE's shared INSERT grammar, but execute the original statement unchanged.
            source = "INSERT" + sql[tokens[0].end + 1 : tokens[-1].end + 1]
        rename = starter == "RENAME"
        if rename:
            positions = [
                index
                for index, token in enumerate(tokens)
                if token.text.upper() == "TO" and token.token_type != TokenType.IDENTIFIER
            ]
            if len(tokens) < 5 or tokens[1].text.upper() != "TABLE" or len(positions) != 1:
                raise PolicyError("Only a single RENAME TABLE pair is supported.")
            pivot = positions[0]
            if pivot < 3 or pivot + 1 >= len(tokens):
                raise PolicyError("Invalid RENAME TABLE statement.")
            source = (
                "ALTER TABLE "
                + sql[tokens[2].start : tokens[pivot].start]
                + "RENAME TO "
                + sql[tokens[pivot + 1].start : tokens[-1].end + 1]
            )
        statements = sqlglot.parse(source, read="mysql", error_level=sqlglot.ErrorLevel.RAISE)
        trees = [item for item in statements if item is not None]
        if len(trees) != 1:
            raise PolicyError("Exactly one SQL statement is required.")
        tree = trees[0]
        _check_tree(tree, database)
        if isinstance(tree, exp.Show):
            target_db = tree.args.get("db")
            if target_db is not None and target_db.name != database:
                raise PolicyError("Direct references must use the configured database.")
        if mode == "query":
            allowed = _is_read(tree)
        elif mode == "write":
            allowed = isinstance(tree, (exp.Insert, exp.Update, exp.Delete))
        elif mode == "ddl":
            allowed = isinstance(tree, (exp.Create, exp.Alter, exp.Drop)) and str(
                tree.args.get("kind", "")
            ).upper() in {"TABLE", "INDEX"}
            allowed = allowed or (
                isinstance(tree, exp.TruncateTable) and not tree.args.get("is_database")
            )
            if rename:
                actions = tree.args.get("actions", [])
                allowed = allowed and len(actions) == 1 and isinstance(actions[0], exp.AlterRename)
        else:
            allowed = False
        if not allowed:
            raise PolicyError("Statement is not allowed in this SQL mode.")
        return sql.strip()
    except PolicyError:
        raise
    except (
        SqlglotError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
        UnicodeError,
    ) as exc:
        raise PolicyError("SQL could not be parsed safely as a supported MySQL statement.") from exc
