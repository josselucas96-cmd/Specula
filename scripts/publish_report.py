"""Publish a monthly report: HTML -> PDF (headless Chromium) -> Supabase Storage
-> row in `research` (what the site's Documents section reads).

    python scripts/publish_report.py visionnaire --month 2026-06
    python scripts/publish_report.py batisseur  --month 2026-06 --dry-run

Refuses to publish a DRAFT (no reports/comments/<pid>/<YYYY-MM>.toml): a
report without management commentary is not a report. Idempotent: if a
research row for the same portfolio + month already exists it is updated,
and the PDF object is overwritten.

Runs in CI (.github/workflows/publish-report.yml) or locally if Chromium works.
"""
import argparse
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import generate_monthly_report as gmr  # noqa: E402  (connects to Supabase at import)

BUCKET = "research-docs"


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


from scripts.render_pdf import html_to_pdf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("portfolio")
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    report_date = gmr.month_end(a.month)
    _, _, is_draft = gmr.load_comments(a.portfolio, report_date)
    if is_draft:
        print(f"REFUSED: no commentary file reports/comments/{a.portfolio}/{a.month}.toml "
              f"-> this would publish a DRAFT. Write the commentary first.")
        return 2

    portfolio = gmr.fetch_portfolio(a.portfolio)
    name = portfolio["name"]
    title = gmr.report_title(name, a.month)
    today = date.today().isoformat()
    object_name = f"{today}_{slug(title)}.pdf"

    out_dir = ROOT / "reports" / "published"
    html_path = gmr.generate(a.portfolio, report_date=report_date, out_dir=out_dir)
    pdf_path = html_path.with_suffix(".pdf")
    html_to_pdf(html_path, pdf_path)
    size_kb = pdf_path.stat().st_size / 1024
    print(f"PDF rendered: {pdf_path} ({size_kb:.0f} KB)")

    summary = (f"Monthly report for {name} as of {report_date}: NAV, monthly returns, "
               f"allocation, contributors/detractors, risk metrics and management commentary.")
    if a.dry_run:
        print(f"DRY RUN — would upload {BUCKET}/{object_name} and upsert research row: {title!r}")
        return 0

    sb = gmr.sb
    sb.storage.from_(BUCKET).upload(object_name, pdf_path.read_bytes(),
                                    {"content-type": "application/pdf", "upsert": "true"})
    file_url = sb.storage.from_(BUCKET).get_public_url(object_name)

    existing = (sb.table("research").select("id").eq("portfolio_id", a.portfolio)
                .eq("doc_type", "Portfolio Document").eq("title", title).execute().data)
    row = {"title": title, "doc_type": "Portfolio Document", "portfolio_id": a.portfolio,
           "summary": summary, "file_url": file_url, "status": "published", "published_at": today}
    if existing:
        sb.table("research").update(row).eq("id", existing[0]["id"]).execute()
        print(f"research row updated (id {existing[0]['id']})")
    else:
        sb.table("research").insert(row).execute()
        print("research row inserted")
    print(f"PUBLISHED: {file_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
