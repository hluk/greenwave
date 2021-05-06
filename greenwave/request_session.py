import logging

import requests

from json import dumps
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, ConnectTimeout, RetryError
from urllib3.util.retry import Retry
from urllib3.exceptions import ProxyError, SSLError

from flask import current_app, has_app_context
from requests_futures.sessions import FuturesSession

from greenwave import __version__

log = logging.getLogger(__name__)


class ErrorResponse(requests.Response):
    def __init__(self, status_code, error_message, url):
        super().__init__()
        self.status_code = status_code
        self._error_message = error_message
        self.url = url
        self.reason = error_message.encode()

    @property
    def content(self):
        return dumps({'message': self._error_message}).encode()


class RequestsSession(FuturesSession):
    def request(self, *args, **kwargs):  # pylint:disable=arguments-differ
        future = self.request_future(self, *args, **kwargs)
        return self.response(future)

    def response(self, future):
        try:
            return future.result()
        except (ConnectTimeout, RetryError) as e:
            ret_val = ErrorResponse(504, str(e), future.request.url)
        except (ConnectionError, ProxyError, SSLError) as e:
            ret_val = ErrorResponse(502, str(e), future.request.url)
        return ret_val

    def request_future(self, *args, **kwargs):  # pylint:disable=arguments-differ
        kwargs.setdefault('headers', {'Content-Type': 'application/json'})
        if has_app_context():
            kwargs.setdefault('timeout', current_app.config['REQUESTS_TIMEOUT'])
            kwargs.setdefault('verify', current_app.config['REQUESTS_VERIFY'])

        return super().request(*args, **kwargs)


def get_requests_session():
    """ Get http(s) session for request processing.  """

    session = RequestsSession()
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
        method_whitelist=Retry.DEFAULT_METHOD_WHITELIST.union(('POST',)),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers["User-Agent"] = f"greenwave {__version__}"
    return session
