# -*- coding: utf-8 -*-
"""Who visits the site, and where they come from.

    python scripts/visits_report.py            # last 30 days
    python scripts/visits_report.py 90         # last 90 days

A visitor is one browser (anonymous id). A visit is one browser on one day: the
site's menu reloads the page, so every page opens a new Streamlit session.
The owner's browsers (opened once with ?me=1) are left out, past visits included,
and so are test sessions (?src=test-...).
"""
import os, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)

from utils.data import get_client  # noqa: E402


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sb = get_client()
    owners = {r["visitor"] for r in sb.table("site_visit_owners").select("visitor").execute().data}
    rows, start = [], 0
    while True:
        chunk = (sb.table("site_visits").select("ts,page,src,visitor,session").gte("ts", since)
                 .order("ts").range(start, start + 999).execute().data)
        rows += chunk
        if len(chunk) < 1000:
            break
        start += 1000
    # Sessions opened by our own tests carry ?src=test-... and are left out too.
    test_sessions = {r["session"] for r in rows if (r["src"] or "").startswith("test-")}
    rows = [r for r in rows if r["visitor"] not in owners and r["session"] not in test_sessions]
    def visit_key(r):                    # one browser on one day (session if no id)
        return (r["visitor"] or r["session"], r["ts"][:10])
    visits = {visit_key(r) for r in rows}
    visitors = {r["visitor"] for r in rows if r["visitor"]}
    print(f"Last {days} days, owner and tests excluded: {len(visits)} visits, "
          f"{len(visitors)} browsers, {len(rows)} page views")

    first_src = {}
    for r in rows:                       # source = the tag the visit arrived with
        k = visit_key(r)
        if k not in first_src or (first_src[k] == "(none)" and r["src"]):
            first_src[k] = r["src"] or "(none)"
    print("\nVisits by source:")
    for s, n in Counter(first_src.values()).most_common():
        print(f"  {s:<24} {n}")

    print("\nPage views by page:")
    for p, n in Counter(r["page"] for r in rows).most_common():
        print(f"  {p:<24} {n}")

    by_day = defaultdict(set)
    for r in rows:
        by_day[r["ts"][:10]].add(visit_key(r))
    print("\nVisits by day:")
    for d in sorted(by_day):
        print(f"  {d}  {len(by_day[d])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
