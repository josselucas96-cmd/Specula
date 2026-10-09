"""HTTP layer of the Supabase clients (utils/sb_http.py). No network.

On 2026-10-09 the Bâtisseur page crashed with httpx.ReadError: the cached
client reused a connection Supabase had dropped while idle, and nothing
retried a network error."""
import httpx
import pytest

from utils.sb_http import RetryReadsTransport, make_http_client


class FlakyTransport(httpx.BaseTransport):
    """Fails the first `failures` requests on the connection, then answers 200."""
    def __init__(self, failures, exc=httpx.ReadError):
        self.failures, self.exc, self.calls = failures, exc, 0

    def handle_request(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc("connection dropped", request=request)
        return httpx.Response(200, json=[{"ok": True}], request=request)


def _client(inner):
    return httpx.Client(transport=RetryReadsTransport(inner), base_url="https://example.supabase.co")


@pytest.mark.parametrize("exc", [httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError])
def test_read_on_a_dropped_connection_is_retried_once(exc):
    inner = FlakyTransport(failures=1, exc=exc)
    r = _client(inner).get("/rest/v1/positions")
    assert r.status_code == 200 and inner.calls == 2


def test_read_failing_twice_still_raises():
    inner = FlakyTransport(failures=2)
    with pytest.raises(httpx.ReadError):
        _client(inner).get("/rest/v1/positions")
    assert inner.calls == 2


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_writes_are_never_replayed(method):
    # The write may have reached the database before the connection broke.
    inner = FlakyTransport(failures=1)
    with pytest.raises(httpx.ReadError):
        _client(inner).request(method, "/rest/v1/transactions", json={"a": 1})
    assert inner.calls == 1


def test_supabase_client_uses_the_hardened_http_layer():
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions
    http = make_http_client()
    sb = create_client("https://example.supabase.co", "anon-key",
                       options=SyncClientOptions(httpx_client=http))
    assert sb.postgrest.session is http
    assert isinstance(http._transport, RetryReadsTransport)
