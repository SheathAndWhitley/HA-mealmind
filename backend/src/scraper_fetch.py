"""Robust, SSRF-safe page fetching for the recipe scraper.

Behaviour (inspired by Mealie's fetcher, independently implemented):
  * Only http(s). Every host (including each redirect hop) is resolved first and
    rejected if any address is not public (private, loopback, link-local, CGNAT/
    Tailscale, multicast, reserved). The connection is pinned to the checked IP so
    a second DNS lookup can't point it somewhere else.
  * Tries several browser TLS fingerprints (curl_cffi) in random order. Moves on
    to the next one only when the response looks like a bot block (403/429/503,
    Cloudflare's `cf-mitigated` header, or a challenge page served with 200).
    Real errors such as 404 stop immediately.
  * Short jittered backoff on rate limits, honouring Retry-After (capped).
  * Per-attempt timeout, an overall time budget, and a maximum page size.
"""

import ipaddress
import random
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from curl_cffi import CurlOpt
from curl_cffi import requests as curl_requests

IMPERSONATIONS = ["chrome", "firefox", "safari", "edge"]
ATTEMPT_TIMEOUT = 15          # seconds per request
TOTAL_BUDGET = 45             # seconds for all attempts of one URL
MAX_BYTES = 5 * 1024 * 1024   # recipe pages are well under this
MAX_REDIRECTS = 5

HARD_ERROR_4XX = {404, 410}
RATE_LIMITED = {429, 503}
CHALLENGE_MARKERS = (
    b"__cf_chl", b"cf-browser-verification", b"/cdn-cgi/challenge-platform",
    b"challenges.cloudflare.com", b"_incapsula_resource", b"px-captcha",
    b"perimeterx", b"datadome", b"distil_r_captcha",
)
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # also Tailscale's range


class FetchError(Exception):
    """The page could not be fetched; the message says why."""


class BlockedURLError(FetchError):
    """The URL points at a non-public address or uses a disallowed scheme."""


@dataclass
class Page:
    url: str     # final URL after redirects
    html: str


# --------------------------------------------------------------------------- #
# Address checks
# --------------------------------------------------------------------------- #

def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip in CGNAT:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


def resolve_public(url: str) -> tuple[str, int, str]:
    """Validate a URL and return (host, port, ip) with a public IP to connect to."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURLError(f"only http(s) URLs are allowed: {url}")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"could not resolve {host}") from e

    ips = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    if not ips or not all(_is_public(ip) for ip in ips):
        raise BlockedURLError(f"{host} resolves to a non-public address")
    ip = sorted(ips, key=lambda a: a.version)[0]  # prefer IPv4
    return host, port, (f"[{ip}]" if ip.version == 6 else str(ip))


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def _looks_blocked(status: int, headers, body_start: bytes) -> bool:
    if status == 503 or (400 <= status < 500 and status not in HARD_ERROR_4XX):
        return True
    if "cf-mitigated" in headers:
        return True
    sample = body_start[:4096].lower()
    return any(marker in sample for marker in CHALLENGE_MARKERS)


def _read_capped(response, deadline: float) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise FetchError("page is too large")
    chunks, size = [], 0
    for chunk in response.iter_content():
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_BYTES:
            raise FetchError("page is too large")
        if time.monotonic() > deadline:
            raise FetchError("page took too long to download")
    return b"".join(chunks)


def _attempt(url: str, impersonate: str, deadline: float):
    """One request chain (following redirects by hand, checking every hop).

    Returns (page, None, None) on success, (None, status, retry_after) when blocked,
    and raises FetchError on errors a different fingerprint won't fix.
    """
    for _ in range(MAX_REDIRECTS + 1):
        host, port, ip = resolve_public(url)
        session = curl_requests.Session(
            impersonate=impersonate,
            curl_options={CurlOpt.RESOLVE: [f"{host}:{port}:{ip}"]},
        )
        try:
            response = session.get(url, timeout=ATTEMPT_TIMEOUT, allow_redirects=False, stream=True)
            try:
                status = response.status_code
                if status in (301, 302, 303, 307, 308) and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                body = _read_capped(response, min(deadline, time.monotonic() + ATTEMPT_TIMEOUT))
            finally:
                response.close()
        finally:
            session.close()

        if _looks_blocked(status, response.headers, body):
            return None, status, response.headers.get("retry-after")
        if status >= 400:
            raise FetchError(f"HTTP {status}")
        encoding = response.encoding or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return Page(url=url, html=text), None, None
    raise FetchError("too many redirects")


def fetch_page(url: str) -> Page:
    """Fetch a recipe page, rotating browser fingerprints when a site blocks us."""
    deadline = time.monotonic() + TOTAL_BUDGET
    fingerprints = random.sample(IMPERSONATIONS, len(IMPERSONATIONS))
    last_status = None

    for index, fingerprint in enumerate(fingerprints):
        if time.monotonic() >= deadline:
            break
        try:
            page, last_status, retry_after = _attempt(url, fingerprint, deadline)
        except (curl_requests.RequestsError, OSError) as e:
            raise FetchError(f"network error: {e}") from e
        if page is not None:
            return page
        if last_status in RATE_LIMITED and index < len(fingerprints) - 1:
            delay = 1.0
            try:
                delay = max(delay, float(retry_after or 0))
            except ValueError:
                pass
            delay = min(delay, 5.0) + random.uniform(0, 0.5)
            time.sleep(max(0.0, min(delay, deadline - time.monotonic())))

    raise FetchError(f"blocked by the site (last status {last_status})")
