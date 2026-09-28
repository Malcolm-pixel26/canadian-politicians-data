"""
Nunavut's MLAs, from the Legislative Assembly of Nunavut's website
(assembly.nu.ca) -- the one legislature Represent doesn't cover.

The members page links to a page per MLA with their constituency, any
duties (Premier, minister, Speaker...), their office at the Legislative
Assembly and their constituency office(s). That's one request for the
list plus one per MLA -- 22 seats, about half a minute at a polite pace.

Pages are read by their visible labels ("Constituency", "Duties",
"Legislative Office", "Constituency Office"), not their layout, and a
page without them is skipped rather than guessed at.

Nunavut has consensus government -- no parties -- so party_name is blank,
as it is for the Northwest Territories in Represent. The member pages
belong to the seat, not the person (the Cambridge Bay page went from one
MLA to the next after the 2025 election), so master.xlsx doesn't use
their URLs to recognise people -- see SEAT_PAGE_SETS in represent_common.

scrape_provincial_territorial.py saves them with the other legislatures:
provincial_territorial/nunavut-legislature.* and master.xlsx's
Provincial_Territorial tab, as "Legislative Assembly of Nunavut".
"""
import re
import urllib.parse
from collections import namedtuple
from html.parser import HTMLParser

from represent_common import (
    MAX_CONSECUTIVE_FAILURES,
    FetchError,
    format_phone,
    http_get_html,
)

MEMBERS_URL = "https://www.assembly.nu.ca/members/mla"
SET_NAME = "Legislative Assembly of Nunavut"
SLUG = "nunavut-legislature"  # file name, in the style of Represent's sets
OFFICE = "MLA"
PROVINCE = "NU"

_HOSTS = ("assembly.nu.ca", "www.assembly.nu.ca")
_MEMBER_PATH_RE = re.compile(r"^/node/\d+/?$")

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "nav",
    "ol", "p", "section", "table", "td", "th", "tr", "ul",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
              "track", "wbr"}

_HEADINGS = ("duties", "legislative office", "constituency office")
_LABEL_RE = re.compile(r"\b(phone|telephone|tel|fax|e-?mail|toll[- ]free|cell|mobile)\s*:\s*", re.IGNORECASE)
_LABEL_KINDS = {"phone": "phone", "telephone": "phone", "tel": "phone", "fax": "fax",
                "email": "email", "e-mail": "email"}
_POSTAL_CODE_RE = re.compile(r"\b[A-Z]\d[A-Z] ?\d[A-Z]\d\b")
_EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_HONORIFIC_RE = re.compile(r"^(?:the\s+)?(?:honourable|honorable|hon\.)\s+", re.IGNORECASE)
_CONSTITUENCY_RE = re.compile(r"^constituency\s*:?\s*(.*)$", re.IGNORECASE)
_VACANT_RE = re.compile(r"^vacant\b", re.IGNORECASE)  # "VACANT", "Vacant Constituency: ..."
_NOT_A_CONSTITUENCY = ("office", "map")  # "Constituency Office", "Constituency Map"
# Duties are short titles; a line this long is the biography.
MAX_DUTY_LENGTH = 150


# --- Reading the pages ------------------------------------------------------------

class Page:
    """A page as text blocks (each paragraph, heading, div... a list of its
    lines, split at <br>), plus its <title>, first <h1>, links as (href,
    inside a Drupal listing?) and images as (block number, src)."""

    def __init__(self, title, heading, blocks, links, images):
        self.title = title
        self.heading = heading
        self.blocks = blocks
        self.links = links
        self.images = images

    def lines(self):
        """[(block number, line)] in page order."""
        return [(b, line) for b, block in enumerate(self.blocks) for line in block]


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.heading = None  # the first <h1>'s text, once it's closed
        self.blocks, self.links, self.images = [], [], []
        self._text = []
        self._skip = 0
        self._in_title = False
        self._open = []  # (tag, is a Drupal "views" listing element)
        self._mailto = None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        if self._skip:
            return
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == "h1" and self.heading is None:
            self.heading = len(self.blocks)  # where its text will be
        if tag == "br":
            self._text.append("\n")
        elif tag == "a":
            href = (attrs.get("href") or "").strip()
            if href.lower().startswith("mailto:"):
                self._mailto = (href[len("mailto:"):].split("?")[0], len(self._text))
            elif href:
                self.links.append((href, any(listing for _, listing in self._open)))
        elif tag == "img" and attrs.get("src"):
            self.images.append((len(self.blocks), attrs["src"].strip()))
        if tag not in _VOID_TAGS:
            self._open.append((tag, "views-" in (attrs.get("class") or "")))

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
            return
        if tag == "a" and self._mailto:
            address, start = self._mailto
            if "@" not in "".join(self._text[start:]):
                self._text.append(f" {address}")  # a link reading "Email" -- keep the address
            self._mailto = None
        if tag in _BLOCK_TAGS:
            self._flush()
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                del self._open[i:]
                break

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        else:
            self._text.append(data)

    def _flush(self):
        lines = (" ".join(line.split()) for line in "".join(self._text).split("\n"))
        lines = [line for line in lines if line]
        self._text = []
        if lines:
            self.blocks.append(lines)

    def close(self):
        super().close()
        self._flush()


def parse_page(page_text):
    parser = _PageParser()
    parser.feed(page_text)
    parser.close()
    heading = parser.heading
    heading = " ".join(parser.blocks[heading]) if heading is not None and heading < len(parser.blocks) else ""
    return Page(" ".join(parser.title.split()), heading, parser.blocks, parser.links, parser.images)


# --- The members page ------------------------------------------------------------

def member_links(page, base=MEMBERS_URL):
    """Links to MLAs' pages (…/node/1234) in page order, without repeats:
    those inside the site's listing of members if it has one, otherwise
    every such link on the page (non-member pages are weeded out when
    they're read)."""
    def member_url(href):
        url = urllib.parse.urljoin(base, href)
        parts = urllib.parse.urlsplit(url)
        if parts.hostname in _HOSTS and _MEMBER_PATH_RE.match(parts.path):
            return urllib.parse.urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", ""))
        return None

    found = [(member_url(href), listing) for href, listing in page.links]
    found = [(url, listing) for url, listing in found if url]
    if any(listing for _, listing in found):
        found = [(url, listing) for url, listing in found if listing]
    return list(dict.fromkeys(url for url, _ in found))


def vacant_seats(page):
    """How many seats the members page shows as vacant."""
    return sum(1 for _, line in page.lines() if _VACANT_RE.match(line))


# --- A member's page -------------------------------------------------------------

def _heading(line):
    """The section heading a line is ("legislative office"...), or None."""
    key = line.strip().rstrip(":").strip().lower()
    return key if key in _HEADINGS else None


def split_labels(line):
    """
    A line of an office's details as (the text before any label, [(kind,
    value)]): "Arviat, NU X0C 0E0 Phone: (867) 857-4201 Fax: (867) 857-4205"
    -> ("Arviat, NU X0C 0E0", [("phone", "(867) 857-4201"), ("fax", ...)]).
    Kinds are phone, fax, email, or the label itself (e.g. "toll free").
    """
    matches = list(_LABEL_RE.finditer(line))
    if not matches:
        return line.strip(" ,;"), []
    labelled = []
    for match, following in zip(matches, matches[1:] + [None]):
        value = line[match.end():following.start() if following else len(line)].strip(" ,;")
        label = match.group(1).lower()
        labelled.append((_LABEL_KINDS.get(label, label), value))
    return line[:matches[0].start()].strip(" ,;"), labelled


def _is_contact_block(block):
    return any(_LABEL_RE.search(line) or _POSTAL_CODE_RE.search(line) for line in block)


def _contact_lines(lines, heading_block):
    """An office section's lines, up to where the biography starts: the
    first block (after the heading's own) with no phone, fax, email or
    postal code in it."""
    blocks = {}
    for block, line in lines:
        blocks.setdefault(block, []).append(line)
    kept = []
    for block, line in lines:
        if block != heading_block and not _is_contact_block(blocks[block]):
            break
        kept.append((block, line))
    return kept


def parse_offices(lines):
    """
    [(block, line)] of an office section -> [{"address", "phone", "fax",
    "email"}], one per office. A new office starts when an address line
    follows one that already has a phone, fax or email -- which works
    whether each office is its own paragraph or they share one.
    """
    offices = []
    for _, line in lines:
        address, labelled = split_labels(line)
        current = offices[-1] if offices else None
        if current is None or (address and any(current[k] for k in ("phone", "fax", "email"))):
            current = {"address": [], "phone": "", "fax": "", "email": ""}
            offices.append(current)
        if address:
            current["address"].append(address)
        for kind, value in labelled:
            if kind == "email":
                found = _EMAIL_RE.search(value)
                value = found.group(0) if found else ""
            elif kind in ("phone", "fax"):
                value = format_phone(value)
            if kind in current and not current[kind] and value:
                current[kind] = value
    for office in offices:
        office["address"] = ", ".join(office["address"])
    return [o for o in offices if any(o.values())]


def _name_from_title(title):
    """"Honourable John Main | Nunavut Legislative Assembly" -> "John Main"."""
    return _HONORIFIC_RE.sub("", title.split("|")[0].strip()).strip()


def parse_member_page(page, url):
    """
    One MLA's page as a flat row (CSV_FIELDS), or None if it isn't an
    MLA's page -- no "Constituency" plus a duties or office section -- or
    it's a vacant seat's.
    """
    name = _name_from_title(page.title or page.heading)
    if not name or name.lower() == "vacant":
        return None
    lines = page.lines()
    # Skip the site's header: read from the page's own heading (the name) on.
    start = next((i for i, (_, line) in enumerate(lines) if _name_from_title(line) == name), -1) + 1

    constituency, at = "", None
    for i in range(start, len(lines)):
        match = _CONSTITUENCY_RE.match(lines[i][1])
        if not match or match.group(1).strip().lower().startswith(_NOT_A_CONSTITUENCY):
            continue
        constituency = match.group(1).strip()
        if not constituency and i + 1 < len(lines) and not _heading(lines[i + 1][1]):
            constituency = lines[i + 1][1]
        at = i
        break
    if not constituency:
        return None

    sections = {}
    headings = [(i, _heading(line)) for i, (_, line) in enumerate(lines) if i > at and _heading(line)]
    for (i, heading), following in zip(headings, headings[1:] + [(len(lines), None)]):
        if heading in sections:
            continue
        section = lines[i + 1:following[0]]
        if heading == "duties":
            section = section[:next((n for n, (_, line) in enumerate(section) if len(line) > MAX_DUTY_LENGTH),
                                    len(section))]
        else:
            section = _contact_lines(section, lines[i][0])
        sections[heading] = section
    if not sections:
        return None

    duties = [line for _, line in sections.get("duties", [])]
    legislative = (parse_offices(sections.get("legislative office", [])) or [{}])[0]
    constituency_offices = parse_offices(sections.get("constituency office", []))
    local = constituency_offices[0] if constituency_offices else {}
    email = legislative.get("email") or local.get("email", "")

    extra = []
    if local.get("email") and local["email"] != email:
        extra.append(("constituency_email", local["email"]))
    if local.get("fax"):
        extra.append(("constituency_fax", local["fax"]))
    for n, office in enumerate(constituency_offices[1:], start=2):
        details = ", ".join(v for v in (office["address"], office["phone"], office["email"]) if v)
        extra.append((f"constituency_office_{n}", details))

    first_block = lines[start][0] if start < len(lines) else 0
    photo = next((src for block, src in page.images
                  if block >= first_block - 1 and "member" in urllib.parse.urlsplit(src).path.lower()), "")
    first, _, last = name.rpartition(" ")
    return {
        "category": "provincial_territorial",
        "representative_set": SET_NAME,
        "name": name,
        "first_name": first,
        "last_name": last,
        "elected_office": OFFICE,
        "district_name": constituency,
        "party_name": "",
        "email": email,
        "phone": legislative.get("phone", ""),
        "postal_address": legislative.get("address", ""),
        "url": url,
        "personal_url": "",
        "photo_url": urllib.parse.urljoin(url, photo) if photo else "",
        "gender": "",
        "source_url": MEMBERS_URL,
        "province_territory": PROVINCE,
        "constituency_phone": local.get("phone", ""),
        "constituency_address": local.get("address", ""),
        "roles": "; ".join(duties),
        "preferred_languages": "",
        "extra_info": "; ".join(f"{key}: {value}" for key, value in extra),
    }


# --- Fetching ----------------------------------------------------------------------

NunavutResult = namedtuple("NunavutResult", "rows complete vacant")


def fetch_members():
    """
    Every MLA as a row for master.xlsx, as NunavutResult(rows, complete,
    vacant): `complete` is False if any member's page couldn't be
    downloaded (so nobody should be marked as gone), `vacant` the number
    of seats the site shows as vacant. Raises FetchError if the members
    page can't be downloaded, ValueError if no MLAs could be read from it.
    """
    print("Fetching Nunavut's MLAs from assembly.nu.ca (Represent doesn't have them)...")
    listing = parse_page(http_get_html(MEMBERS_URL))
    urls = member_links(listing)
    if not urls:
        raise ValueError("the members page has no links to MLAs' pages any more -- "
                         "the Assembly may have changed its website")

    rows, failed, consecutive, last_error = [], 0, 0, None
    for i, url in enumerate(urls, start=1):
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            print(f"  ! assembly.nu.ca seems to be unreachable -- skipping the remaining {len(urls) - i + 1} page(s).")
            failed += len(urls) - i + 1
            break
        try:
            page = parse_page(http_get_html(url))
        except FetchError as e:
            print(f"  ! skipping {url}: {e}")
            failed, consecutive, last_error = failed + 1, consecutive + 1, e
            continue
        consecutive = 0
        row = parse_member_page(page, url)
        if row:
            rows.append(row)

    if not rows:
        if last_error:
            raise last_error
        raise ValueError("none of the pages the members page links to read like an MLA's page -- "
                         "the Assembly may have changed its website")
    vacant = vacant_seats(listing)
    print(f"  -> {SET_NAME}: {len(rows)} MLA(s)" + (f", {vacant} seat(s) vacant" if vacant else ""))
    return NunavutResult(rows, failed == 0, vacant)
