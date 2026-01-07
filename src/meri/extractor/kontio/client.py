
import contextvars
import uuid
from datetime import datetime

import requests
from pytz import utc
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from meri.settings import settings


class KontioApiClient(requests.Session):
    """
    Custom requests.Session for Kontio API with default headers and helpers.
    """
    def __init__(self):
        super().__init__()

        retries = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504]
        )

        self.mount('http://', HTTPAdapter(max_retries=retries))
        self.mount('https://', HTTPAdapter(max_retries=retries))

        self.headers.update({
            "User-Agent": settings.BOT_USER_AGENT,
            "x-kontio-app-version": "1.1.36",
            "Accept-Encoding": "gzip",
            "x-kontio-app-device-id": str(uuid.uuid4()),
            "x-kontio-app-device-model": "Android SDK built for arch64",
            "Content-Type": "application/json; charset=utf-8",
            "x-kontio-app-os": "android",
            "Accept": "application/json",
            "x-kontio-app-os-version": "13",
        })

    def get(self, url: str | bytes, **kwargs):
        """
        Perform a GET request with Kontio API-specific headers.
        """
        headers = kwargs.pop("headers", {})
        headers.setdefault("x-kontio-app-request-timestamp", datetime.now(utc).isoformat() + "Z")
        headers.setdefault("x-kontio-app-request-id", str(uuid.uuid4()))
        ret = super().get(url, headers=headers, **kwargs)
        # Check type of response
        if ret.headers.get("Content-Type") != "application/json":
            raise ValueError(f"Unexpected Content-Type: {ret.headers.get('Content-Type')}")
        return ret


# Singleton factory using contextvars
_client_ctx: contextvars.ContextVar["KontioApiClient"] = contextvars.ContextVar(f"{__name__}.kontio_client", default=KontioApiClient())

def client() -> KontioApiClient:
    client = _client_ctx.get()
    if client is None:
        client = KontioApiClient()
        _client_ctx.set(client)
    return client
