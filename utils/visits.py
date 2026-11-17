"""Anonymous visit counter (requested 2026-11-17: know who arrives from LinkedIn).

One row per page and per Streamlit session, written in a background thread so a
page never waits for it and never fails because of it. Nothing personal is
stored: no IP address and no account, only the page, an optional source tag
taken from the link (?src=linkedin) and an anonymous browser id, which is a
salted hash of a cookie Streamlit itself sets. The id lets visits from the same
browser be grouped; it cannot be traced back to a person.

Not recorded:
  * headless browsers and bots (our keep-awake and watchdog jobs use one);
  * local runs on localhost (set SPECULA_VISITS_LOCAL=1 to test them);
  * the owner. Opening the site once with ?me=1 marks the current browser as
    the owner's in site_visit_owners; the report leaves those browsers out,
    past visits included, and the session that carries ?me=1 is not logged.

The pure helpers below are unit-tested (tests/test_visits.py).
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid

BOT_MARKERS = ("headless", "bot", "crawler", "spider", "preview", "facebookexternalhit",
               "python-requests", "python-httpx", "curl", "wget", "playwright")
# Cookies this site sets in the browser, in order of preference. None of them
# identifies a person. _streamlit_xsrf is present from the very first page but
# Streamlit re-masks it on every load ("2|mask|masked token|time"): the token
# under the mask is what stays the same, so it is unmasked before hashing.
# ajs_anonymous_id is stable too but only appears from the second page on.
ID_COOKIES = ("_streamlit_xsrf", "ajs_anonymous_id")
_SALT = "specula-visits-v1"
_SRC_BAD = re.compile(r"[^a-z0-9_\-]")


def is_bot(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return not ua or any(m in ua for m in BOT_MARKERS)


def is_local(host: str | None) -> bool:
    h = (host or "").split(":")[0].strip().lower()
    return h in ("", "localhost", "127.0.0.1", "0.0.0.0", "::1")


def clean_src(value) -> str | None:
    """Source tag from the link, normalised: lower case, letters, digits, - and _
    only, at most 40 characters. None when absent or empty after cleaning."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not value:
        return None
    s = _SRC_BAD.sub("", str(value).strip().lower())[:40]
    return s or None


def unmask_xsrf(value: str) -> str:
    """The stable token inside a Tornado version-2 XSRF cookie; the value itself
    for any other format."""
    parts = (value or "").split("|")
    if len(parts) == 4 and parts[0] == "2":
        try:
            mask, masked = bytes.fromhex(parts[1]), bytes.fromhex(parts[2])
            if mask:
                return bytes(b ^ mask[i % len(mask)] for i, b in enumerate(masked)).hex()
        except ValueError:
            pass
    return value


def visitor_id(cookies) -> str | None:
    """Anonymous, stable id for the browser: 16 hex characters of a salted hash."""
    for name in ID_COOKIES:
        v = (cookies or {}).get(name)
        if v:
            if name == "_streamlit_xsrf":
                v = unmask_xsrf(v)
            return hashlib.sha256(f"{_SALT}|{name}|{v}".encode()).hexdigest()[:16]
    return None


def should_log(headers, local_override: bool = False) -> bool:
    headers = headers or {}
    ua = headers.get("User-Agent") or headers.get("user-agent")
    host = headers.get("Host") or headers.get("host")
    if is_bot(ua):
        return False
    if is_local(host) and not local_override:
        return False
    return True


def _insert(sb, table: str, row: dict, upsert: bool = False) -> None:
    try:
        q = sb.table(table)
        (q.upsert(row, ignore_duplicates=True) if upsert else q.insert(row)).execute()
    except Exception:
        pass  # a visit counter must never break or slow a page


def log_visit(page: str) -> None:
    """Record one visit of `page` for this session. Safe to call on every rerun."""
    import streamlit as st

    ss = st.session_state
    if ss.get("_visit_owner"):
        return
    seen = ss.setdefault("_visit_pages", set())
    if page in seen:
        return
    seen.add(page)

    ctx = st.context
    if not should_log(ctx.headers, os.environ.get("SPECULA_VISITS_LOCAL") == "1"):
        return

    from utils.data import get_client
    sb = get_client()
    vid = visitor_id(ctx.cookies)
    if clean_src(st.query_params.get("me")) == "1":
        ss["_visit_owner"] = True
        if vid:
            threading.Thread(target=_insert, args=(sb, "site_visit_owners", {"visitor": vid}, True),
                             daemon=True).start()
        return

    sid = ss.setdefault("_visit_session", uuid.uuid4().hex[:16])
    row = {"page": str(page)[:40], "src": clean_src(st.query_params.get("src")),
           "visitor": vid, "session": sid}
    threading.Thread(target=_insert, args=(sb, "site_visits", row), daemon=True).start()
