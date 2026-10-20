"""Print the CREATE TABLE statements for the two global tables, so a catalog admin can create them once
and give the app's service principal only SELECT + MODIFY.

    python scripts/print_ddl.py --catalog prd_mega --schema sgpbpi163 > sql/create_tables.sql

Nothing is executed and no connection is made.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", required=True)
    args = ap.parse_args()
    # Only to satisfy the configuration check; the statements are built, never sent.
    os.environ.update(CBT_BACKEND="databricks", CBT_CATALOG=args.catalog, CBT_SCHEMA=args.schema,
                      DATABRICKS_SERVER_HOSTNAME="unused.example.net", DATABRICKS_HTTP_PATH="/unused", DATABRICKS_TOKEN="unused")
    from src import constants as C
    from src import queries as q
    q.reset()
    backend = q._get_backend()
    print("-- Climate Budget Tagging: global tables (methodology version %s)" % C.METHODOLOGY_VERSION)
    print("-- Grants for the app's service principal: USE CATALOG, USE SCHEMA, SELECT, MODIFY on these tables\n")
    for kind in C.KINDS:
        print(backend.create_table_sql(kind) + ";\n")


if __name__ == "__main__":
    main()
