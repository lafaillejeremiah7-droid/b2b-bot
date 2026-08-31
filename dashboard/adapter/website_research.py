from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dashboard.services.discovery_handoff import ResearchHandoff, ScoutHandoff

MAX_HTML_BYTES = 1_000_000
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
CONTACT_HINTS = ("contact", "get-in-touch", "get_in_touch", "about", "support")
CTA_HINTS = (
    "contact",
    "book",
    "schedule",
    "quote",
    "estimate",
    "call",
    "get started",
    "request",
)


class WebsiteResearchError(RuntimeError):
    """Raised when Researcher cannot verify enough evidence to continue."""


@dataclass(frozen=True)
class FetchedPage:
    requested_url: str
    final_url: str
    html: str


FetchTransport = Callable[[str, float], FetchedPage]


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.mailto_emails: list[str] = []
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self.has_viewport = False
        self.has_form = False
        self.h1_count = 0
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        attr = {key.casefold(): (value or "") for key, value in attrs}
        if name in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if name == "title":
            self._in_title = True
        elif name == "meta" and attr.get("name", "").casefold() == "viewport":
            self.has_viewport = bool(attr.get("content", "").strip())
        elif name == "a":
            href = attr.get("href", "").strip()
            if href:
                self.hrefs.append(href)
                if href.casefold().startswith("mailto:"):
                    address = href[7:].split("?", 1)[0].strip()
                    if address:
                        self.mailto_emails.append(address)
        elif name == "form":
            self.has_form = True
        elif name == "h1":
            self.h1_count += 1

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif name == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.text_parts.append(text)
        if self._in_title:
            self.title_parts.append(text)

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()


def _validate_public_http_url(url: str) -> str:
    """Reject local/private destinations before Researcher performs network I/O."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebsiteResearchError("Researcher requires an absolute HTTP(S) website URL.")
    if parsed.username is not None or parsed.password is not None:
        raise WebsiteResearchError("Researcher website URLs may not contain credentials.")

    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise WebsiteResearchError("Researcher refuses localhost/private-network website targets.")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise WebsiteResearchError("Researcher refuses localhost/private-network website targets.")
        return parsed.geturl()

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise WebsiteResearchError("Researcher website URL has an invalid port.") from exc

    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebsiteResearchError("Researcher could not resolve the official website hostname.") from exc
    if not addresses:
        raise WebsiteResearchError("Researcher could not resolve the official website hostname.")

    resolved_ips: set[str] = set()
    for entry in addresses:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        resolved_ips.add(str(sockaddr[0]))
    if not resolved_ips:
        raise WebsiteResearchError("Researcher could not resolve the official website hostname.")
    for raw_ip in resolved_ips:
        try:
            address = ipaddress.ip_address(raw_ip.split("%", 1)[0])
        except ValueError as exc:
            raise WebsiteResearchError("Researcher resolved an invalid website address.") from exc
        if not address.is_global:
            raise WebsiteResearchError("Researcher refuses localhost/private-network website targets.")
    return parsed.geturl()


class _PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(url: str, timeout: float) -> FetchedPage:
    safe_url = _validate_public_http_url(url)
    request = Request(
        safe_url,
        headers={
            "User-Agent": "B2B-Bot-Researcher/0.1 (+website quality review)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    opener = build_opener(_PublicRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 - destination validated above and on redirect
            final_url = _validate_public_http_url(response.geturl())
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.casefold():
                raise WebsiteResearchError("Official website did not return HTML content.")
            raw = response.read(MAX_HTML_BYTES + 1)
            if len(raw) > MAX_HTML_BYTES:
                raise WebsiteResearchError("Website HTML exceeds the Researcher safety limit.")
            charset = response.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
            return FetchedPage(safe_url, final_url, html)
    except HTTPError as exc:
        raise WebsiteResearchError(f"Website returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise WebsiteResearchError(f"Website transport error: {exc.reason}") from exc


def _same_site(base_url: str, candidate_url: str) -> bool:
    base = (urlparse(base_url).hostname or "").casefold().removeprefix("www.")
    candidate = (urlparse(candidate_url).hostname or "").casefold().removeprefix("www.")
    return bool(base and candidate and (candidate == base or candidate.endswith(f".{base}")))


def _parse(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    return parser


def _candidate_contact_urls(page: FetchedPage, parser: _PageParser) -> list[str]:
    urls: list[str] = []
    for href in parser.hrefs:
        lower = href.casefold()
        if lower.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(page.final_url, href)
        if not _same_site(page.final_url, absolute):
            continue
        path = urlparse(absolute).path.casefold()
        if any(hint in path for hint in CONTACT_HINTS) and absolute not in urls:
            urls.append(absolute)
    return urls


def _emails_from_page(page: FetchedPage, parser: _PageParser) -> set[str]:
    emails = {email.strip().casefold() for email in parser.mailto_emails if email.strip()}
    emails.update(match.group(0).casefold() for match in EMAIL_RE.finditer(parser.visible_text))
    return {
        email
        for email in emails
        if not email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
    }


def _preferred_email(emails: set[str], website_url: str) -> str:
    if not emails:
        return ""
    domain = (urlparse(website_url).hostname or "").casefold().removeprefix("www.")
    same_domain = sorted(
        email for email in emails
        if email.rsplit("@", 1)[-1].removeprefix("www.") == domain
    )
    pool = same_domain or sorted(emails)
    preferred_prefixes = ("info@", "contact@", "hello@", "sales@", "office@")
    for prefix in preferred_prefixes:
        for email in pool:
            if email.startswith(prefix):
                return email
    return pool[0]


def _verified_observations(parser: _PageParser) -> tuple[str, ...]:
    observations: list[str] = []
    text = parser.visible_text.casefold()
    href_text = " ".join(parser.hrefs).casefold()

    if not parser.has_viewport:
        observations.append("The homepage HTML does not declare a responsive viewport meta tag.")
    if not any(href.casefold().startswith("tel:") for href in parser.hrefs):
        observations.append("The homepage does not expose a click-to-call telephone link.")
    if not any(hint in text or hint in href_text for hint in CTA_HINTS):
        observations.append("The homepage does not expose a clear contact, quote, booking, or scheduling call-to-action.")
    if not parser.has_form:
        observations.append("The homepage does not include an inquiry, booking, or quote form.")
    if parser.h1_count == 0:
        observations.append("The homepage HTML does not contain an H1 heading.")
    if not parser.title:
        observations.append("The homepage is missing a descriptive HTML title.")
    elif len(parser.title) > 70:
        observations.append(f"The homepage title is {len(parser.title)} characters long, which is unusually long for a page title.")

    return tuple(observations)


class WebsiteResearchClient:
    """Researcher adapter for businesses that already have an official website.

    It verifies only facts visible in fetched HTML. It refuses to continue when
    it cannot verify a public business email or at least two concrete website
    observations, preventing the Personalizer from inventing criticisms.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        fetch: FetchTransport | None = None,
        max_pages: int = 3,
    ) -> None:
        if not 1 <= max_pages <= 5:
            raise ValueError("max_pages must be between 1 and 5.")
        self._timeout = timeout_seconds
        self._fetch = fetch or _default_fetch
        self._max_pages = max_pages

    def research(self, scout: ScoutHandoff) -> ResearchHandoff:
        if not scout.candidate_website:
            raise WebsiteResearchError(
                "Scout found no candidate website. A separate no-website verifier is required."
            )

        home = self._fetch(scout.candidate_website, self._timeout)
        if not _same_site(scout.candidate_website, home.final_url):
            raise WebsiteResearchError("Official website redirected to an unrelated domain.")
        home_parser = _parse(home.html)
        pages = [(home, home_parser)]

        for url in _candidate_contact_urls(home, home_parser):
            if len(pages) >= self._max_pages:
                break
            try:
                page = self._fetch(url, self._timeout)
            except WebsiteResearchError:
                continue
            if not _same_site(home.final_url, page.final_url):
                continue
            pages.append((page, _parse(page.html)))

        emails: set[str] = set()
        evidence_urls = list(scout.evidence_urls)
        for page, parser in pages:
            emails.update(_emails_from_page(page, parser))
            if page.final_url not in evidence_urls:
                evidence_urls.append(page.final_url)

        contact_email = _preferred_email(emails, home.final_url)
        if not contact_email:
            raise WebsiteResearchError("Researcher could not verify a public business email on the official website.")

        observations = _verified_observations(home_parser)
        if len(observations) < 2:
            raise WebsiteResearchError(
                "Researcher found fewer than two concrete website issues; do not manufacture outreach claims."
            )

        return ResearchHandoff(
            scout_digest=scout.digest,
            contact_email=contact_email,
            website=scout.candidate_website,
            contact_verified=True,
            website_verified=True,
            website_observations=observations,
            evidence_urls=tuple(evidence_urls),
        )
