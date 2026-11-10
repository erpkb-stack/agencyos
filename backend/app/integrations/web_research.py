"""Safe fetching of a prospect's *public* website and factual signal extraction.

Safety rules (each refusal is a ``BlockedError`` with a clear reason):
* http/https only, default ports only, no credentials in URLs.
* The host must resolve only to public internet addresses; private, loopback, link-local,
  multicast and reserved ranges (and names like ``localhost``/``*.internal``) are refused. This is
  re-checked for every redirect, so a public page cannot bounce the fetch into our network.
* robots.txt is honoured for our user agent; the user agent identifies AgencyOS honestly.
* Size, time, content-type and per-site request-rate limits.

Residual risk (documented): DNS can change between our check and the HTTP connection
(DNS rebinding). Run AgencyOS where the network itself blocks access to internal services if that
matters to you.

Everything fetched is *untrusted data*: it is reduced to plain facts and short text excerpts here,
and agents receive it inside the untrusted-data block, never as instructions.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

Resolver = Callable[[str], list[str]]

ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")
MAX_REDIRECTS = 4
INTERESTING_PATHS = ("contact", "book", "schedule", "service", "about", "pricing", "quote", "appointment")


class BlockedError(Exception):
    """Refused by a safety rule or robots.txt. Not retried."""


class FetchError(Exception):
    """Network/HTTP problem fetching the page."""


def default_resolver(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve {host}") from exc


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolver: Resolver) -> None:
    """Raise BlockedError unless ``url`` is a fetchable public http(s) URL."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedError(f"Only http and https websites can be checked (got {parts.scheme or 'none'})")
    if parts.username or parts.password:
        raise BlockedError("URLs with credentials are not allowed")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise BlockedError("The URL has no host name")
    try:
        port = parts.port
    except ValueError as exc:
        raise BlockedError("The URL has an invalid port") from exc
    if port not in (None, 80, 443):
        raise BlockedError("Only standard web ports (80 and 443) are allowed")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is None and (host == "localhost" or host.endswith(BLOCKED_SUFFIXES) or "." not in host):
        raise BlockedError(f"'{host}' is not a public website")
    addresses = [str(literal)] if literal else resolver(host)
    if not addresses:
        raise FetchError(f"Could not resolve {host}")
    for address in addresses:
        if not _is_public_ip(address):
            raise BlockedError(f"'{host}' points to a private or reserved address; it can't be checked")


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    html: str
    bytes: int
    elapsed_ms: int


class Fetcher:
    """Fetches pages with the safety rules above. One instance can serve many checks (thread-safe)."""

    def __init__(self, client: httpx.Client, *, resolver: Resolver = default_resolver, user_agent: str,
                 max_bytes: int = 1_500_000, min_interval: float = 1.0) -> None:
        self.client = client
        self.resolver = resolver
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.min_interval = min_interval
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    # -- politeness ------------------------------------------------------------------------

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last_hit.get(host, 0) + self.min_interval - now
            self._last_hit[host] = max(now, now + wait)
        if wait > 0:
            time.sleep(wait)

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                page = self._get(origin + "/robots.txt", allow_any_type=True, max_bytes=64_000)
                if page.status in (401, 403):
                    parser = None  # treat as "disallow all"
                elif page.status >= 400:
                    parser.parse([])  # no robots.txt: everything allowed
                else:
                    parser.parse(page.html.splitlines())
            except FetchError:
                parser.parse([])  # unreachable robots.txt: the page fetch itself will report errors
            self._robots[origin] = parser
        parser = self._robots[origin]
        return False if parser is None else parser.can_fetch(self.user_agent, url)

    # -- fetching --------------------------------------------------------------------------

    def _get(self, url: str, *, allow_any_type: bool = False, max_bytes: int | None = None) -> Page:
        limit = max_bytes or self.max_bytes
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, self.resolver)
            self._wait_turn(urlsplit(current).hostname or "")
            started = time.monotonic()
            try:
                with self.client.stream("GET", current, follow_redirects=False,
                                        headers={"User-Agent": self.user_agent, "Accept": "text/html,text/plain;q=0.9"}) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                        current = urljoin(current, resp.headers["location"])
                        continue
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not allow_any_type and resp.status_code < 400 and ctype not in ALLOWED_TYPES:
                        raise BlockedError(f"Not a web page ({ctype or 'unknown type'})")
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > limit:
                            chunks.append(chunk[: max(0, limit - (size - len(chunk)))])
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    encoding = resp.encoding or "utf-8"
                    return Page(url=current, status=resp.status_code, content_type=ctype,
                                html=raw.decode(encoding, errors="replace"), bytes=size,
                                elapsed_ms=int((time.monotonic() - started) * 1000))
            except httpx.TimeoutException as exc:
                raise FetchError(f"Timed out fetching {current}") from exc
            except httpx.HTTPError as exc:
                raise FetchError(f"Could not fetch {current}: {type(exc).__name__}") from exc
        raise FetchError("Too many redirects")

    def fetch_page(self, url: str) -> Page:
        if not self.allowed_by_robots(url):
            raise BlockedError("The site's robots.txt asks automated tools not to read this page")
        return self._get(url)


# -- HTML → facts ------------------------------------------------------------------------------


class _Parser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.viewport = False
        self.h1: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.forms: list[list[dict[str, str]]] = []
        self.text: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_h1 = False
        self._in_a: str | None = None
        self._a_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
            self.h1.append("")
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name == "description":
                self.description = a.get("content", "")[:400]
            elif name == "viewport" and "width=device-width" in a.get("content", "").replace(" ", "").lower():
                self.viewport = True
        elif tag == "a":
            self._in_a = a.get("href", "")
            self._a_text = []
        elif tag == "form":
            self.forms.append([])
        elif tag in ("input", "textarea", "select") and self.forms:
            self.forms[-1].append({"tag": tag, "type": a.get("type", "text").lower(), "name": a.get("name", "")[:40]})

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "a" and self._in_a is not None:
            self.links.append((self._in_a, " ".join("".join(self._a_text).split())[:80]))
            self._in_a = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        if self._in_h1 and self.h1:
            self.h1[-1] += data
        if self._in_a is not None:
            self._a_text.append(data)
        if data.strip():
            self.text.append(data.strip())


def parse(html: str) -> _Parser:
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML still yields whatever was parsed
        pass
    return parser


PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
YEAR_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.IGNORECASE)
MARKERS = {
    "online_booking": ("book online", "schedule online", "book now", "schedule service", "schedule now",
                       "request appointment", "calendly.com", "acuityscheduling", "housecallpro", "servicetitan",
                       "getjobber", "setmore", "squareup.com/appointments", "booksy"),
    "live_chat": ("intercom", "drift.com", "tawk.to", "livechatinc", "crisp.chat", "tidio", "zdassets", "podium"),
    "analytics": ("googletagmanager.com", "gtag(", "google-analytics.com", "fbq(", "connect.facebook.net",
                  "hotjar", "plausible.io", "clarity.ms"),
    "reviews": ("testimonial", "google reviews", "customer reviews", "5-star", "five star", "reviews"),
}
CTA_WORDS = ("call", "quote", "book", "schedule", "contact", "estimate", "appointment", "get started")


def extract_signals(pages: list[Page], today: date | None = None) -> dict[str, Any]:
    """Deterministic facts about the site. Booleans, counts and short strings only."""
    today = today or date.today()
    home = pages[0]
    parsed = [parse(p.html) for p in pages]
    raw = " ".join(p.html.lower() for p in pages)
    visible = " ".join(" ".join(pp.text) for pp in parsed)
    hp = parsed[0]
    links = [href for pp in parsed for href, _ in pp.links]
    link_texts = [t.lower() for pp in parsed for _, t in pp.links]
    forms = [f for pp in parsed for f in pp.forms]
    contact_forms = [f for f in forms if len(f) >= 2 and any(
        i["type"] in ("email", "tel") or i["tag"] == "textarea" or i["name"].lower() in ("email", "phone", "name")
        for i in f)]
    years = [int(y) for y in YEAR_RE.findall(raw)]
    year = max(years) if years else None
    signals: dict[str, Any] = {
        "https": home.url.startswith("https://"),
        "mobile_viewport": hp.viewport,
        "has_contact_form": bool(contact_forms),
        "click_to_call": any(h.lower().startswith("tel:") for h in links),
        "phone_shown": bool(PHONE_RE.search(visible)),
        "email_shown": any(h.lower().startswith("mailto:") for h in links) or bool(EMAIL_RE.search(visible)),
        "online_booking": any(m in raw for m in MARKERS["online_booking"]),
        "live_chat": any(m in raw for m in MARKERS["live_chat"]),
        "analytics": any(m in raw for m in MARKERS["analytics"]),
        "mentions_reviews": any(m in visible.lower() for m in MARKERS["reviews"]),
        "has_h1": any(h.strip() for h in hp.h1),
        "meta_description": bool(hp.description.strip()),
        "cta_links": sum(any(w in t for w in CTA_WORDS) for t in link_texts),
        "copyright_year": year,
        "response_ms": home.elapsed_ms,
        "page_kb": round(home.bytes / 1024),
        "pages_checked": len(pages),
    }
    signals["conversion_flags"] = conversion_flags(signals, today)
    signals["conversion_gap_score"] = min(100, sum(f["weight"] for f in signals["conversion_flags"]))
    return signals


def conversion_flags(s: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Plain-language issues that commonly cost local service businesses leads (a heuristic)."""
    rules = [
        (not s["has_contact_form"], 20, "No contact or quote form found"),
        (not s["click_to_call"], 15, "Phone number is not click-to-call on mobile"),
        (not s["online_booking"], 15, "No online booking or scheduling found"),
        (not s["mobile_viewport"], 15, "Page is not set up for mobile screens"),
        (not s["https"], 10, "Site does not use HTTPS"),
        (not s["live_chat"], 5, "No live chat or text-us widget found"),
        (s["cta_links"] == 0, 10, "No clear call-to-action links (call, quote, book, contact)"),
        (s["copyright_year"] is not None and s["copyright_year"] < today.year - 1, 5,
         f"Copyright year {s['copyright_year']} suggests the site isn't maintained"),
        (not s["meta_description"], 3, "No search description (meta description)"),
        (s["response_ms"] > 3000, 7, f"Slow first response ({s['response_ms'] / 1000:.1f} s)"),
        (not s["analytics"], 5, "No analytics found, so lead sources likely aren't measured"),
    ]
    return [{"issue": text, "weight": weight} for hit, weight, text in rules if hit]


def pick_subpages(home_url: str, parsed_home: _Parser, limit: int) -> list[str]:
    """Same-site links that most likely describe services, booking or contact (deduplicated)."""
    host = urlsplit(home_url).hostname
    seen, ranked = {home_url.rstrip("/")}, []
    for href, _text in parsed_home.links:
        absolute = urljoin(home_url, href.split("#")[0])
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or parts.hostname != host:
            continue
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", "")).rstrip("/")
        if clean in seen:
            continue
        rank = next((i for i, word in enumerate(INTERESTING_PATHS) if word in parts.path.lower()), None)
        if rank is not None:
            seen.add(clean)
            ranked.append((rank, clean))
    return [u for _, u in sorted(ranked)][:limit]


@dataclass
class Research:
    final_url: str
    http_status: int
    title: str
    description: str
    signals: dict[str, Any]
    pages: list[dict[str, Any]] = field(default_factory=list)
    text_excerpt: str = ""


def research_site(url: str, fetcher: Fetcher, max_pages: int = 3) -> Research:
    """Fetch the homepage and up to ``max_pages - 1`` relevant same-site pages, then extract facts."""
    start = normalize_url(url)
    home = fetcher.fetch_page(start)
    if home.status >= 400:
        raise FetchError(f"The website answered with HTTP {home.status}")
    pages = [home]
    home_parsed = parse(home.html)
    for sub in pick_subpages(home.url, home_parsed, max(0, max_pages - 1)):
        try:
            page = fetcher.fetch_page(sub)
        except (BlockedError, FetchError):
            continue
        if page.status < 400:
            pages.append(page)
    texts = [" ".join(parse(p.html).text) for p in pages]
    excerpt = (texts[0][:4000] + "".join(f"\n\n[{p.url}]\n{t[:1500]}" for p, t in zip(pages[1:], texts[1:])))[:8000]
    return Research(
        final_url=home.url, http_status=home.status, title=" ".join(home_parsed.title.split())[:300],
        description=home_parsed.description, signals=extract_signals(pages),
        pages=[{"url": p.url, "status": p.status, "kb": round(p.bytes / 1024), "ms": p.elapsed_ms} for p in pages],
        text_excerpt=excerpt,
    )
