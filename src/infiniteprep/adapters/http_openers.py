"""HTTP opener that follows no redirect, for requests that carry a credential or a fixed target.

urllib follows redirects by default and copies the request headers to the new location,
Authorization included. A redirect from a provider host can therefore hand a token to
another host, or move an https request to plain http. With this opener a redirect response
raises urllib.error.HTTPError instead, so a request never leaves the host it names.
"""

from __future__ import annotations

import urllib.request
from typing import Any, Final


def build_no_redirect_opener() -> urllib.request.OpenerDirector:
    """Return an opener with the standard handlers except the redirect handler.

    Without HTTPRedirectHandler, HTTPDefaultErrorHandler turns a 3xx response into an
    HTTPError, so the redirect is never followed.
    """
    opener = urllib.request.OpenerDirector()
    for handler_class in (
        urllib.request.ProxyHandler,
        urllib.request.HTTPHandler,
        urllib.request.HTTPSHandler,
        urllib.request.HTTPDefaultErrorHandler,
        urllib.request.HTTPErrorProcessor,
        urllib.request.UnknownHandler,
    ):
        opener.add_handler(handler_class())
    return opener


# One opener serves every caller, the same way urlopen shares its global opener.
NO_REDIRECT_OPENER: Final[urllib.request.OpenerDirector] = build_no_redirect_opener()


def open_without_redirects(request: urllib.request.Request, timeout: float) -> Any:
    """Send request through the no-redirect opener and return the response.

    A redirect response raises urllib.error.HTTPError. Callers use the result as a context
    manager, as they do with urlopen.
    """
    return NO_REDIRECT_OPENER.open(request, timeout=timeout)
