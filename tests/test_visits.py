"""Visit counter (utils/visits.py): what is recorded and what is not.

Requested 2026-11-17: know how many people arrive from LinkedIn, without the
owner's own visits and without our keep-awake robot (a headless browser that
opens the site every 6 hours).
"""
from utils.visits import clean_src, is_bot, is_local, should_log, unmask_xsrf, visitor_id

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
HEADLESS = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/131.0 Safari/537.36"


def test_robots_are_not_counted():
    assert is_bot(HEADLESS)                     # keep-awake / watchdog
    assert is_bot("LinkedInBot/1.0 (compatible; Mozilla/5.0)")
    assert is_bot("")                           # no user agent at all
    assert not is_bot(CHROME)


def test_local_runs_are_not_counted_unless_asked():
    assert not should_log({"User-Agent": CHROME, "Host": "localhost:8599"})
    assert should_log({"User-Agent": CHROME, "Host": "localhost:8599"}, local_override=True)
    assert should_log({"User-Agent": CHROME, "Host": "specula-project.streamlit.app"})
    assert not should_log({"User-Agent": HEADLESS, "Host": "specula-project.streamlit.app"})
    assert is_local("127.0.0.1") and not is_local("specula-project.streamlit.app")


def test_source_tag_is_cleaned():
    assert clean_src("LinkedIn") == "linkedin"
    assert clean_src(["li-bmnr"]) == "li-bmnr"
    assert clean_src("<script>") == "script"
    assert clean_src("x" * 80) == "x" * 40
    assert clean_src("") is None and clean_src(None) is None and clean_src("!!!") is None


def test_browser_id_is_anonymous_and_stable():
    a = visitor_id({"_streamlit_xsrf": "2|abcd|1234|1700000000"})
    assert a == visitor_id({"_streamlit_xsrf": "2|abcd|1234|1700000000"})
    assert len(a) == 16 and "abcd" not in a
    assert a != visitor_id({"_streamlit_xsrf": "2|ffff|9999|1700000000"})
    assert visitor_id({}) is None and visitor_id(None) is None


def _xsrf(token: bytes, mask: bytes, ts: str = "1763397000") -> str:
    masked = bytes(b ^ mask[i % len(mask)] for i, b in enumerate(token))
    return f"2|{mask.hex()}|{masked.hex()}|{ts}"


def test_same_browser_same_id_although_the_cookie_changes_on_every_page():
    """Regression 2026-11-17 (local test): Streamlit re-masks its XSRF cookie on
    every page load, so hashing the raw cookie gave one browser a new id per
    page and the owner could never be excluded."""
    token = bytes(range(16))
    page1 = _xsrf(token, bytes.fromhex("01020304"))
    page2 = _xsrf(token, bytes.fromhex("a0b1c2d3"))
    assert page1 != page2
    assert unmask_xsrf(page1) == unmask_xsrf(page2) == token.hex()
    assert visitor_id({"_streamlit_xsrf": page1}) == visitor_id({"_streamlit_xsrf": page2,
                                                                  "ajs_anonymous_id": "abc"})
    other = _xsrf(bytes(range(1, 17)), bytes.fromhex("01020304"))
    assert visitor_id({"_streamlit_xsrf": other}) != visitor_id({"_streamlit_xsrf": page1})
    assert unmask_xsrf("not-a-v2-cookie") == "not-a-v2-cookie"
