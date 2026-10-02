"""Single-send transport shared by Docs mutations and comment creation."""

from http.client import HTTPException

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.errors import HttpError

from gdoc.util import AuthError, GdocError

_UNCERTAIN = (
    "Comment write outcome is uncertain; inspect the document's comments before "
    "retrying. No fallback comment was created."
)


class _SingleSendHttp(httplib2.Http):
    """Keep httplib2's response handling, but forbid every second wire send."""

    _sent = False
    uncertainty = _UNCERTAIN
    on_send = None

    def _conn_request(self, conn, request_uri, method, body, headers):
        transport = self

        class Connection:
            def __getattr__(self, name):
                return getattr(conn, name)

            def request(self, *args, **kwargs):
                if transport._sent:
                    raise GdocError(transport.uncertainty)
                transport._sent = True
                if transport.on_send is not None:
                    transport.on_send()
                return conn.request(*args, **kwargs)

        return super()._conn_request(Connection(), request_uri, method, body, headers)


def execute_mutation_request(
    request, *, uncertainty="Write outcome is uncertain", on_send=None
):
    """Use fresh transport state; never change the cached service's HTTP client.

    num_retries=0 alone does not disable httplib2's lost-response replay.
    The connection guard also covers redirects and internal reconnect attempts.
    Credentials may refresh before sending, but a 401 is surfaced without replay.
    ``on_send`` runs immediately before the single wire send, so a caller can
    record that the request left; a failure before that point never did.
    """
    transport = _SingleSendHttp(timeout=request.http.http.timeout)
    transport.uncertainty = uncertainty
    transport.on_send = on_send
    http = AuthorizedHttp(
        request.http.credentials, http=transport, max_refresh_attempts=0
    )
    # Token refresh uses the original auth transport, not the comment send budget.
    http._request = request.http._request
    try:
        result = request.execute(http=http, num_retries=0)
    except HttpError as exc:
        if int(exc.resp.status) >= 500:
            raise GdocError(uncertainty) from exc
        raise
    except (OSError, HTTPException, httplib2.HttpLib2Error) as exc:
        if transport._sent:
            raise GdocError(uncertainty) from exc
        raise GdocError(
            f"network error ({exc}); the write was not sent. "
            "Check the connection and try again"
        ) from exc
    except GdocError:
        raise
    except RefreshError as exc:
        # Credentials are refreshed before the send; a refresh failure means
        # nothing left the wire, so keep the re-authentication guidance.
        if transport._sent:
            raise GdocError(uncertainty) from exc
        raise AuthError(f"Authentication expired ({exc}). Run `gdoc auth`.") from exc
    except TransportError as exc:
        if transport._sent:
            raise GdocError(uncertainty) from exc
        raise GdocError(
            f"network error while refreshing credentials ({exc}); "
            "the write was not sent. Check the connection and try again"
        ) from exc
    except Exception as exc:
        # The bytes left the wire; a response we cannot parse (empty or
        # malformed body in the postprocessor) may still have saved the comment.
        if transport._sent:
            raise GdocError(uncertainty) from exc
        raise
    finally:
        transport.close()
    # Valid JSON that is not an object (null, a list) parses fine but says
    # nothing about whether the comment saved.
    if not isinstance(result, dict):
        raise GdocError(uncertainty)
    return result


def execute_comment_request(request):
    """Keep the comment-specific recovery instructions on the shared transport."""
    return execute_mutation_request(request, uncertainty=_UNCERTAIN)
