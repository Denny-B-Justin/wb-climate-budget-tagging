"""Print the CREATE TABLE statements for the two global tables, so a catalog admin can create them once
and give the app's service principal only SELECT + MODIFY.

    python scripts/print_ddl.py --catalog prd_mega --schema sgpbpi163 > sql/create_tables.sql

Nothing is executed and no connection is made.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import constants as C  # noqa: E402


def _lit(text: str) -> str:
    """Escape a string for a single-quoted Spark SQL literal."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def create_table_sql(catalog: str, schema: str, kind: str) -> str:
    cols = ",\n  ".join("`%s` %s COMMENT '%s'" % (c.name, c.sql, _lit(c.doc)) for c in C.TABLE_SCHEMAS[kind])
    return ("CREATE TABLE IF NOT EXISTS `%s`.`%s`.`%s` (\n  %s\n) USING DELTA COMMENT '%s' "
            "TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true', 'delta.autoOptimize.autoCompact' = 'true')"
            % (catalog, schema, C.TABLE_NAMES[kind], cols, _lit(C.TABLE_COMMENTS[kind])))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", required=True)
    args = ap.parse_args()
    print("-- Climate Budget Tagging: global tables (methodology version %s)" % C.METHODOLOGY_VERSION)
    print("-- Grants for the app's service principal: USE CATALOG, USE SCHEMA, SELECT, MODIFY on these tables\n")
    for kind in C.KINDS:
        print(create_table_sql(args.catalog, args.schema, kind) + ";\n")


if __name__ == "__main__":
    main()
