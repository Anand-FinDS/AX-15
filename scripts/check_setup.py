"""AX-15 setup check.

Run from the project root after creating .env:
    python scripts/check_setup.py            # one-line paid pings (a fraction of a cent)
    python scripts/check_setup.py --free     # key + auth + model lookup only, no tokens (what the console uses)

Checks each provider and the database and prints PASS / FAIL / SKIP. Never prints secret values.
The checks live in ax15/status.py, shared with the console status sidebar.
"""
from __future__ import annotations

import sys

from ax15 import status


def main(argv: list[str]) -> int:
    paid = "--free" not in argv
    checks = [status.anthropic_api(paid), status.openai_api(paid), status.luna(paid), status.groq(paid),
              status.postgres("DATABASE_URL"), status.postgres("DATABASE_URL_DIRECT"), status.langfuse(),
              status.dataset(), *status.agent_versions()]
    width = max(len(c.name) for c in checks)
    print("\nAX-15 setup check" + ("" if paid else " (free: no paid calls)") + "\n" + "-" * (width + 30))
    for c in checks:
        word = {True: "PASS", False: "FAIL", None: "SKIP"}[c.ok]
        print(f"{c.name:<{width}}  {word:<5} {c.detail}")
    failed = [c.name for c in checks if c.ok is False and not c.name.startswith("Postgres DATABASE_URL_DIRECT")]
    print("-" * (width + 30))
    print("All required checks passed." if not failed else f"{len(failed)} check(s) failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
