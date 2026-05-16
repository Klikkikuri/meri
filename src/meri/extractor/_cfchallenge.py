"""
Cloudflare anti-scraping / challenge detection.

Detection logic is based on patterns observed in FlareSolverr:
https://github.com/FlareSolverr/FlareSolverr
"""

import re
from enum import Enum

class CloudflareStatus(Enum):
    OK = "ok"
    CHALLENGE = "challenge"
    BLOCKED = "blocked"

    def __bool__(self) -> bool:
        """Treat status as truthy only when request is OK (no challenge/block)."""
        return True if self is CloudflareStatus.OK else False

# Page titles that indicate a Cloudflare (or similar) challenge page.
_CHALLENGE_TITLES = [
    "just a moment...",  # Cloudflare JS / managed challenge
    "ddos-guard",        # DDoS-Guard
]

# Page titles that indicate Cloudflare has outright blocked the request.
_ACCESS_DENIED_TITLES = [
    "access denied",
    "403 forbidden",
]

# Substrings / patterns in the HTML body that indicate a challenge is present.
_CHALLENGE_BODY_PATTERNS: list[re.Pattern[str]] = [
    # Cloudflare challenge JS object
    re.compile(r"window\._cf_chl_opt\s*=", re.IGNORECASE),
    # Cloudflare challenge platform script
    re.compile(r"/cdn-cgi/challenge-platform/", re.IGNORECASE),
    # Cloudflare managed-challenge element IDs
    re.compile(r'id=["\']cf-challenge-running["\']', re.IGNORECASE),
    re.compile(r'id=["\']cf-please-wait["\']', re.IGNORECASE),
    re.compile(r'id=["\']challenge-spinner["\']', re.IGNORECASE),
    re.compile(r'id=["\']trk_jschal_js["\']', re.IGNORECASE),
    re.compile(r'id=["\']turnstile-wrapper["\']', re.IGNORECASE),
    # Cloudflare Turnstile hidden input
    re.compile(r'name=["\']cf-turnstile-response["\']', re.IGNORECASE),
    # hCaptcha served by Cloudflare
    re.compile(r'cloudflare\.hcaptcha\.com', re.IGNORECASE),
    # Cloudflare error detail selector
    re.compile(r'id=["\']cf-error-details["\']', re.IGNORECASE),
    # DDoS-Guard challenge
    re.compile(r'ddos-guard\.net', re.IGNORECASE),
]

# Substrings that indicate the request was outright blocked (not just challenged).
_ACCESS_DENIED_BODY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'id=["\']cf-error-details["\']', re.IGNORECASE),
    re.compile(r"error\s+code:\s+1020", re.IGNORECASE),  # Cloudflare "Access denied" error 1020
]


def _extract_title(html: str) -> str:
    """Return the lowercased page title from raw HTML, or an empty string."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip().lower() if m else ""


def is_cloudflare_challenge(html: str) -> bool:
    """
    Return True if *html* appears to be a Cloudflare (or similar) anti-scraping
    challenge / CAPTCHA page.

    Detection checks (modelled after FlareSolverr):
      1. Page title matches a known challenge title.
      2. HTML body contains known challenge markers (JS objects, element IDs,
         challenge-platform scripts, hCaptcha / Turnstile widgets, …).
    """
    title = _extract_title(html)
    for challenge_title in _CHALLENGE_TITLES:
        if title == challenge_title:
            return True

    for pattern in _CHALLENGE_BODY_PATTERNS:
        if pattern.search(html):
            return True

    return False


def is_cloudflare_blocked(html: str) -> bool:
    """
    Return True if Cloudflare has *blocked* the request outright (e.g. error 1020
    / "Access denied"), as opposed to presenting a solvable challenge.
    """
    title = _extract_title(html)
    for denied_title in _ACCESS_DENIED_TITLES:
        if title.startswith(denied_title):
            return True

    for pattern in _ACCESS_DENIED_BODY_PATTERNS:
        if pattern.search(html):
            return True

    return False


def cloudflare_status(html: str) -> CloudflareStatus | bool:
    """
    Convenience helper that returns a :class:`CloudflareStatus` enum value:

    * ``CloudflareStatus.BLOCKED``   – Cloudflare has denied the request entirely.
    * ``CloudflareStatus.CHALLENGE`` – A solvable Cloudflare challenge / CAPTCHA was detected.
    * ``CloudflareStatus.OK``        – No Cloudflare protection detected.
    """
    if is_cloudflare_blocked(html):
        return CloudflareStatus.BLOCKED
    if is_cloudflare_challenge(html):
        return CloudflareStatus.CHALLENGE
    return CloudflareStatus.OK
