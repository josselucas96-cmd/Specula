"""HTTP layer shared by the app's Supabase clients.

supabase-py opens an HTTP/2 connection and the app keeps it for its whole life
(`get_client` is cached). When Supabase or a proxy drops that connection while
it sits idle, the next query is sent into a dead socket and fails with
`httpx.ReadError`. postgrest only retries HTTP 503/520 responses, never a
network error, so the exception reached the visitor: on 2026-10-09 the
Bâtisseur page showed a full traceback until it was reloaded.

Two defences:
  * HTTP/1.1 with a short keep-alive. Before reusing a pooled HTTP/1.1
    connection, httpcore checks whether the server has closed it and opens a
    new one if so; its HTTP/2 path makes no such check.
  * One retry of a read (GET/HEAD) that still fails on the connection itself.
    Writes are never retried: a write may have reached the database before the
    connection broke, and replaying it could record a trade twice.
No Streamlit import here, so the logic is testable on its own.
"""
import httpx

# Failures of the connection itself, as opposed to an HTTP error response.
STALE_CONNECTION_ERRORS = (httpx.ReadError, httpx.WriteError,
                           httpx.RemoteProtocolError, httpx.ConnectError)
READ_METHODS = ("GET", "HEAD")


class RetryReadsTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport, retries: int = 1):
        self.inner = inner
        self.retries = retries

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        attempt = 0
        while True:
            try:
                return self.inner.handle_request(request)
            except STALE_CONNECTION_ERRORS:
                if request.method not in READ_METHODS or attempt >= self.retries:
                    raise
                attempt += 1     # the pool has discarded the dead connection

    def close(self) -> None:
        self.inner.close()


def make_http_client() -> httpx.Client:
    """httpx client for SyncClientOptions(httpx_client=...). Timeouts mirror
    postgrest's defaults (120 s) so slow ledger reads behave as before."""
    inner = httpx.HTTPTransport(http2=False, limits=httpx.Limits(keepalive_expiry=30.0))
    return httpx.Client(transport=RetryReadsTransport(inner),
                        timeout=httpx.Timeout(120.0, connect=10.0),
                        follow_redirects=True)
