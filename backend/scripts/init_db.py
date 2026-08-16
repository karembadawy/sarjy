#!/usr/bin/env python3
"""Create Sarjy's six tables in Supabase. Idempotent — safe to run any number of times.

`create_all` only issues CREATE TABLE for tables that do not exist yet, so this never
drops or alters anything. It is not a migration tool: if a column changes later, change it
in the Supabase SQL editor (or add Alembic) — this script will not notice.

Run:  cd backend && python scripts/init_db.py
Exit: 0 = the six tables are present · 1 = something went wrong (reason printed)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

from app.config import ConfigError  # noqa: E402


def main() -> int:
    try:
        from app.db import engine
        from app.models import Base
    except ConfigError as exc:
        print(f"\n  configuration error:\n{exc}\n")
        return 1

    expected = sorted(Base.metadata.tables)
    print()
    print(f"  Sarjy · creating tables on {engine.url.host}")
    print()

    try:
        before = set(inspect(engine).get_table_names())
        Base.metadata.create_all(engine)
        after = set(inspect(engine).get_table_names())
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {str(exc).strip().splitlines()[0]}")
        print("  fix: check DATABASE_URL — it must be the Supabase Session pooler URI (D-020).")
        print()
        return 1

    for name in expected:
        state = "created" if name in after - before else "already existed"
        print(f"  · {name:<14} {state}")

    missing = [name for name in expected if name not in after]
    if missing:
        print(f"\n  FAILED: still missing {', '.join(missing)}\n")
        return 1

    print(f"\n  All {len(expected)} tables present.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
