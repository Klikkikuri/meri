"""
Storage drivers read and write one document by its location.
============================================================

A location is a filesystem path, a `file://` URL, a GitHub URL or another `https://` URL. :func:`driver_for`
chooses the driver, so no caller tests a URL prefix itself. A driver also joins a relative `href` against a
location in its own terms: standard URL joining drops the query, and a GitHub Contents API URL keeps its `ref`
there.

Every HTTP read is bounded while it downloads, and no driver follows a redirect. See :meth:`Driver._get`.
"""

import base64
import json
import os
import posixpath
import re
import stat
import tempfile
import time
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests
from niitti import get_logger
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

if TYPE_CHECKING:
    from meri.settings.rahti import RahtiSettings

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30

#: Largest document a driver reads. A larger response is refused while it downloads, before it fills memory.
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024

_CHUNK_SIZE = 64 * 1024


class StoredBlob(NamedTuple):
    """The content of a document, and its version: a GitHub blob sha, an HTTP `ETag`, or `None`."""

    content: bytes
    version: str | None


class BlobNotFoundError(FileNotFoundError):
    """The location holds no document, or the credentials cannot see it."""


def write_atomic(path: Path, content: bytes) -> None:
    """
    Write `content` to `path`, so that a reader never sees a partial file.

    The content goes to a temporary file of its own beside `path`, which then replaces `path`, so concurrent writers
    never share a temporary file. An existing file keeps its permissions; a new file gets 0644.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _is_absolute(href: str) -> bool:
    return bool(urlsplit(href).scheme)


def _refuse_redirect(url: str, response: requests.Response) -> None:
    # Following a redirect would let an allowed `https://` URL send the request on to any address, plain HTTP inside
    # the network included.
    if 300 <= response.status_code < 400:
        raise OSError(f"{url} redirects to {response.headers.get('Location')!r}. Use the final URL.")


class Driver:
    """
    Reads documents at the locations it :meth:`matches`, and writes them when :attr:`writable`.

    :param settings: Credentials and timeout. Only the attributes a driver needs are read.
    """

    name: ClassVar[str]
    weight: ClassVar[int] = 0
    writable: ClassVar[bool] = False

    def __init__(self, settings: "RahtiSettings | None" = None) -> None:
        self.settings = settings

    @classmethod
    def matches(cls, location: str) -> bool:
        raise NotImplementedError

    def join(self, base: str, href: str) -> str:
        """Resolve `href` against the location `base`. An absolute `href` is returned unchanged."""
        raise NotImplementedError

    def read(self, location: str) -> StoredBlob:
        """:raises BlobNotFoundError: If there is no document at `location`."""
        raise NotImplementedError

    def write(self, location: str, content: bytes, *, version: str | None, message: str) -> str | None:
        """
        Store `content`, and return its new version.

        :param version: The version read before, so a concurrent change is refused rather than overwritten.
        :param message: Commit message, for drivers that keep history.
        """
        raise NotImplementedError(f"{self.name} storage is read only: {location}")

    @property
    def timeout(self) -> int:
        return getattr(self.settings, "timeout", None) or DEFAULT_TIMEOUT

    @cached_property
    def session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        session.mount("https://", HTTPAdapter(max_retries=retry))
        # A compressed body could expand past the size limit inside a single read.
        session.headers["Accept-Encoding"] = "identity"
        return session

    def _get(self, url: str, headers: dict[str, str] | None = None) -> tuple[requests.Response, bytes]:
        """
        GET `url` without following a redirect, and read its body within the size limit and :attr:`timeout`.

        The `requests` timeout limits each wait for data, not the whole transfer, so the deadline is checked here.

        :return: The response, whose body is already read and closed, and the body.
        :raises OSError: If `url` redirects, or the transfer does not end within :attr:`timeout` seconds.
        :raises ValueError: If the body is larger than :data:`MAX_DOCUMENT_SIZE`.
        """
        deadline = time.monotonic() + self.timeout
        response = self.session.get(url, headers=headers, timeout=self.timeout, stream=True, allow_redirects=False)
        try:
            _refuse_redirect(url, response)
            body = bytearray()
            # `read1` returns the bytes that have arrived, so a slow sender cannot hold one read past the deadline.
            while chunk := response.raw.read1(_CHUNK_SIZE, decode_content=True):
                body += chunk
                if len(body) > MAX_DOCUMENT_SIZE:
                    raise ValueError(f"Document is larger than {MAX_DOCUMENT_SIZE} bytes: {url}")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Reading {url} took longer than {self.timeout} seconds")
            return response, bytes(body)
        finally:
            response.close()


_drivers: list[type[Driver]] = []


def register[D: type[Driver]](weight: int) -> Callable[[D], D]:
    """Class decorator that makes a driver available to :func:`driver_for`, which tries drivers by descending weight."""

    def decorator(cls: D) -> D:
        cls.weight = weight
        _drivers.append(cls)
        _drivers.sort(key=lambda driver: driver.weight, reverse=True)
        return cls

    return decorator


def driver_for(location: str, settings: "RahtiSettings | None" = None) -> Driver:
    """
    The driver for `location`.

    :raises ValueError: If no driver matches, such as for an `http://` URL.
    """
    for cls in _drivers:
        if cls.matches(location):
            return cls(settings)
    raise ValueError(f"No storage driver for location: {location!r}")


@register(weight=100)
class FileDriver(Driver):
    """Local files, as plain paths or `file://` URLs."""

    name = "file"
    writable = True

    @classmethod
    def matches(cls, location: str) -> bool:
        return urlsplit(location).scheme in ("", "file")

    @staticmethod
    def path(location: str) -> Path:
        return Path(location.removeprefix("file://")).resolve()

    def join(self, base: str, href: str) -> str:
        if _is_absolute(href):
            return href
        prefix = "file://" if base.startswith("file://") else ""
        directory = posixpath.dirname(base.removeprefix("file://"))
        return prefix + posixpath.normpath(posixpath.join(directory, href))

    def read(self, location: str) -> StoredBlob:
        try:
            return StoredBlob(self.path(location).read_bytes(), None)
        except FileNotFoundError as e:
            raise BlobNotFoundError(f"No document at {location}") from e

    def write(self, location: str, content: bytes, *, version: str | None, message: str) -> str | None:
        write_atomic(self.path(location), content)
        return None


class GitHubLocation(NamedTuple):
    owner: str
    repo: str
    ref: str | None
    path: str
    raw_ref: str | None
    """The ref as a raw URL spells it, such as `refs/heads/main`. `None` for a Contents API URL."""


_CONTENTS = re.compile(r"https://api\.github\.com/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/contents/(?P<path>.+)")
# The ref is one path segment in both raw forms. A branch name that contains `/` needs a Contents API URL with `?ref=`.
_RAW = re.compile(
    r"https://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
    r"(?P<raw_ref>refs/(?:heads|tags)/[^/]+|[^/]+)/(?P<path>.+)"
)


@register(weight=50)
class GitHubDriver(Driver):
    """
    Files in a GitHub repository, addressed by a Contents API URL or a raw URL.

    Both forms are read and written through the Contents API: the raw CDN serves copies that can be minutes old,
    and a write needs the blob sha that only the API returns.
    """

    name = "github"
    writable = True

    @staticmethod
    def parse(location: str) -> GitHubLocation:
        parts = urlsplit(location)
        url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if match := _CONTENTS.fullmatch(url):
            ref = parse_qs(parts.query).get("ref", [None])[0]
            return GitHubLocation(match["owner"], match["repo"], ref, match["path"], None)
        if match := _RAW.fullmatch(url):
            raw_ref = match["raw_ref"]
            ref = raw_ref.removeprefix("refs/heads/").removeprefix("refs/tags/")
            return GitHubLocation(match["owner"], match["repo"], ref, match["path"], raw_ref)
        raise ValueError(f"Not a GitHub file location: {location!r}")

    @staticmethod
    def format(location: GitHubLocation) -> str:
        if location.raw_ref is None:
            query = f"?{urlencode({'ref': location.ref})}" if location.ref else ""
            return f"https://api.github.com/repos/{location.owner}/{location.repo}/contents/{location.path}{query}"
        return f"https://raw.githubusercontent.com/{location.owner}/{location.repo}/{location.raw_ref}/{location.path}"

    @classmethod
    def matches(cls, location: str) -> bool:
        try:
            cls.parse(location)
        except ValueError:
            return False
        return True

    def join(self, base: str, href: str) -> str:
        if _is_absolute(href):
            return href
        location = self.parse(base)
        path = posixpath.normpath(posixpath.join(posixpath.dirname(location.path), href))
        if path == ".." or path.startswith("../"):
            raise ValueError(f"{href!r} points outside the repository of {base!r}")
        return self.format(location._replace(path=path))

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"}
        if token := getattr(self.settings, "auth_token", None):
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        return headers

    def read(self, location: str) -> StoredBlob:
        url = self.format(self.parse(location)._replace(raw_ref=None))
        response, body = self._get(url, self._headers())
        if response.status_code == 404:
            # GitHub also answers 404 for a private repository the token cannot see.
            raise BlobNotFoundError(f"No document at {location}, or no access to it")
        response.raise_for_status()

        match json.loads(body):
            case {"type": "file", "sha": str(sha), "encoding": "base64", "content": str(content)}:
                return StoredBlob(base64.b64decode(content), sha)
            case {"type": "file", "sha": str(sha)}:
                # Over 1 MB the envelope has no content (`encoding: none`), so fetch the bytes on their own.
                raw, content = self._get(url, self._headers("application/vnd.github.raw+json"))
                raw.raise_for_status()
                return StoredBlob(content, sha)
            case _:
                raise ValueError(f"Not a file: {location}")

    def write(self, location: str, content: bytes, *, version: str | None, message: str) -> str | None:
        parsed = self.parse(location)
        body: dict = {"message": message, "content": base64.b64encode(content).decode("ascii")}
        if committer := getattr(self.settings, "committer", None):
            body["committer"] = {"name": committer.name, "email": committer.email}
        if version:
            body["sha"] = version
        if parsed.ref:
            body["branch"] = parsed.ref

        url = self.format(parsed._replace(ref=None, raw_ref=None))
        response = self.session.put(
            url, headers=self._headers(), json=body, timeout=self.timeout, allow_redirects=False
        )
        _refuse_redirect(url, response)
        if not response.ok:
            logger.error("GitHub write failed", location=location, status=response.status_code, body=response.text)
        response.raise_for_status()
        return response.json()["content"]["sha"]


@register(weight=0)
class HttpsDriver(Driver):
    """Any other `https://` URL. Read only."""

    name = "https"

    @classmethod
    def matches(cls, location: str) -> bool:
        return urlsplit(location).scheme == "https"

    def join(self, base: str, href: str) -> str:
        return urljoin(base, href)

    def read(self, location: str) -> StoredBlob:
        response, body = self._get(location)
        if response.status_code == 404:
            raise BlobNotFoundError(f"No document at {location}")
        response.raise_for_status()
        return StoredBlob(body, response.headers.get("ETag"))
