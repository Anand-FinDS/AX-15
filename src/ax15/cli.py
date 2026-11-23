"""AX-15 command line.

    ax15 run-batch incoming/<batch> [--agent v1|v2] [--workers 4]   load + run a test batch, write the report
    ax15 clear-batch <batch>                                         delete a panel_test batch from the database

(`python -m ax15 ...` works the same.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _run_batch(a) -> int:
    from ax15.l0_data import batch
    from ax15.settings import ROOT

    folder = Path(a.folder)
    if not folder.is_absolute() and not folder.exists():
        folder = ROOT / folder
    print(f"[batch] {folder.name}: agent {a.agent}", flush=True)
    res = batch.run_batch(folder, agent=a.agent, workers=a.workers,
                          progress=lambda d, n: print(f"[batch] {d}/{n}", flush=True) if d % 5 == 0 or d == n else None)
    s = res.summary()
    print(f"[batch] read {s['rows_read']} · loaded {s['loaded']} · rejected {s['rejected']} · ok {s['ok']} · "
          f"failed {s['failed']} · cost ${s['cost_usd']:.4f}")
    for e in res.intake.rejected:
        print(f"[batch] rejected {e['ref']}: {e['error']}")
    if res.metrics:
        m = res.metrics
        print(f"[batch] scored {m['n']}: routing L1/L2 {m['routing_l1_l2']:.1%}")
    if res.stopped:
        print(f"[batch] STOPPED: {res.stopped}")
    print(f"[batch] wrote {res.report_md.relative_to(ROOT)} and {res.report_csv.relative_to(ROOT)}")
    return 1 if res.stopped else 0


def _clear_batch(a) -> int:
    from ax15 import db
    from ax15.l0_data import batch
    conn, _ = db.connect(autocommit=True)
    n = batch.clear_batch(conn, Path(a.batch).name)
    print(f"[batch] deleted {n} ticket(s) of panel_test batch {Path(a.batch).name!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ax15", description="AX-15 IT support agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("run-batch", help="load a test batch folder, run the agent, write outputs/batch_<name>_report")
    rb.add_argument("folder", help="incoming/<batch>")
    rb.add_argument("--agent", choices=["v1", "v2"], default="v2")
    rb.add_argument("--workers", type=int, default=4)
    rb.set_defaults(fn=_run_batch)
    cb = sub.add_parser("clear-batch", help="delete a panel_test batch (tickets + history) from the database")
    cb.add_argument("batch")
    cb.set_defaults(fn=_clear_batch)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
