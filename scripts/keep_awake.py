"""Keep the public Streamlit app awake with a real browser visit.

Why
---
Streamlit Community Cloud puts an app to sleep after 12 hours without traffic
(threshold lowered from 24h in March 2025). A sleeping app greets visitors with
"Zzzz… get this app back up" and a ~1 minute cold start — unacceptable for a
public track record. Per Streamlit staff, what resets the countdown is a real
visit (or a commit). A plain HTTP GET is not confirmed to count, so we open the
landing page in headless Chromium (a genuine websocket session), and if the
app is asleep we click the wake-up button ourselves.

Scheduled every 2 hours by .github/workflows/keep-awake.yml. It was every 6
hours until 2026-11-25, but GitHub runs only about half of the scheduled times
(gaps of 16 to 18 hours were seen on 11 and 23 November), beyond the 12h limit.
It does NOT write anything: daily_holdings is written by daily_refresh.py only.

Exit code is non-zero when the page could not be reached at all, so the
watchdog / GitHub notifications surface an outage instead of hiding it.
"""
import os
import sys

from playwright.sync_api import sync_playwright

APP_URL = os.environ.get("APP_URL", "https://specula-project.streamlit.app/")
# "Ready" = the app's own content is on screen (the landing page always shows
# the Specula title + THE PORTFOLIOS section). Streamlit's internal test-ids
# vary across versions/custom layouts, so we look at rendered text instead.
# Landing page shows "THE PORTFOLIOS"; any other page shows the nav (Accueil/About) — either counts.
READY_JS = "() => /Specula/.test(document.body.innerText) && /(PORTFOLIO|Accueil|Page not found)/i.test(document.body.innerText)"


def wait_until_rendered(page, timeout_ms: int = 90_000, wake_ms: int = 240_000) -> tuple[bool, bool]:
    """(rendered, woke). On *.streamlit.app the app runs inside an iframe of a
    wrapper page, so the top document never contains the app's text: poll EVERY
    frame. The wake button is looked for on EVERY pass: the sleep page draws it
    a moment after the document is loaded, and looking once, right after the
    load, missed it every time (12 to 17 and 23 to 25 November 2026: the robot
    stared at the button for 90 seconds and the app stayed asleep). A cold
    start takes a minute or two, so a click extends the deadline."""
    import time
    deadline, woke, last_click = time.time() + timeout_ms / 1000, False, 0.0
    while time.time() < deadline:
        for frame in page.frames:
            try:
                if frame.evaluate(READY_JS):
                    return True, woke
            except Exception:
                pass
        if time.time() - last_click > 45 and wake_if_asleep(page):
            if not woke:
                deadline = time.time() + wake_ms / 1000
            woke, last_click = True, time.time()
        page.wait_for_timeout(2_000)
    return False, woke


def wake_if_asleep(page) -> bool:
    for frame in page.frames:
        for label in ("Yes, get this app back up!", "get this app back up"):
            try:
                btn = frame.get_by_role("button", name=label)
                if btn.count() > 0 and btn.first.is_visible():
                    print("app was ASLEEP -> clicking the wake button", flush=True)
                    btn.first.click()
                    return True
            except Exception:
                continue
    return False


def _remote_shas(n: int = 20) -> list:
    """The last `n` commit hashes of origin/main, fetched fresh."""
    import subprocess
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"], check=True, timeout=60)
        out = subprocess.run(["git", "rev-list", "-n", str(n), "origin/main"],
                             check=True, capture_output=True, text=True, timeout=60)
        return out.stdout.split()
    except Exception as e:
        print(f"(could not re-check origin/main: {e})", flush=True)
        return []


def main() -> int:
    print(f"visiting {APP_URL}", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        try:
            page.goto(APP_URL, timeout=120_000, wait_until="domcontentloaded")
            rendered, woke = wait_until_rendered(page)
            if not rendered:
                raise TimeoutError("app content not found in any frame")
            page.wait_for_timeout(8_000)  # let the script run so the session counts as traffic
            title = page.title()
            texts = " ".join((f.evaluate("() => document.body.innerText") or "") for f in page.frames)
            nav = [w for w in ("Accueil", "Moves", "Stock Papers", "Articles", "About") if w in texts]
            import re
            m = re.search(r"build ([0-9a-f]{7,40})", texts)
            build = m.group(1) if m else None
            print(f"app rendered (title: {title!r}){' after wake-up' if woke else ''} | nav items seen: {nav} | "
                  f"page-not-found: {'Page not found' in texts} | build: {build}", flush=True)
            # Deploy drift: the build must be one of the recent commits of main
            # (RECENT_SHAS is set by the workflow). A deploy lags a push by a few
            # minutes, never by ten commits — unless Streamlit stopped pulling.
            recent = os.environ.get("RECENT_SHAS", "").split()
            if build and recent and not any(r.startswith(build) for r in recent):
                # The checkout is a snapshot taken when the job started. A push
                # landing in between deploys within a minute and then looks like
                # drift (false alarm on 2026-09-23). Ask the remote before
                # crying wolf: a red run nobody trusts is worse than no run.
                recent = _remote_shas() or recent
            if build and recent and not any(r.startswith(build) for r in recent):
                print(f"DEPLOY DRIFT: site runs build {build}, not among the last {len(recent)} commits of main "
                      f"-> Streamlit Cloud stopped following pushes. Fix: touch requirements.txt or Reboot app.", flush=True)
                return 2
            return 0
        except Exception as e:
            print(f"ERROR: app did not render: {e}", flush=True)
            try:
                page.screenshot(path="keep_awake_failure.png", full_page=True)
            except Exception:
                pass
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
