"""
Canada's senators, from the Senate's own website (sencanada.ca).

Represent only covers elected bodies, so the Senate comes from the two
tables that https://sencanada.ca/en/senators/ loads into its tabs:

  - List: every sitting senator, with a link to their profile, their
    affiliation (caucus or group), province, appointment date, mandatory
    retirement date (the day they turn 75) and the prime minister on
    whose advice they were appointed;
  - Contact Information: each senator's office phone and email.

The List is the roster. The contact table lags behind it -- when this was
written it still had senators who had retired and nothing yet for that
summer's appointees -- so it only fills in phone numbers and emails for
senators on the List (left blank for anyone it doesn't have yet).

scrape_federal.py saves senators next to the MPs: federal/senators.*
and master.xlsx's Federal tab, as representative set "Senate of Canada",
office "Senator" and their province as the district.
"""
import re
import urllib.parse
from collections import Counter

from represent_common import (
    CSV_FIELDS,
    FetchError,
    find_table,
    format_phone,
    html_tables,
    http_get_html,
    normalize_name,
    province_code,
)

SENATORS_PAGE = "https://sencanada.ca/en/senators/"
_TAB_URL = "https://sencanada.ca/umbraco/surface/SenatorsAjax/GetSenators?Lang=en&displayFor={}"
LIST_URL = _TAB_URL.format("senatorslist")
CONTACTS_URL = _TAB_URL.format("senatorscontactinformation")

SET_NAME = "Senate of Canada"
OFFICE = "Senator"
# Mail to any senator goes here, postage-free (https://sencanada.ca/en/contactus/).
POSTAL_ADDRESS = "Senate of Canada, Ottawa ON K1A 0A4"

# The List's affiliation codes, spelled out as on the Senate's own filters.
# Anything else ("Non-affiliated", a new group) is kept as written.
AFFILIATIONS = {
    "C": "Conservative Party of Canada",
    "CSG": "Canadian Senators Group",
    "GRO": "Government Representative's Office",
    "ISG": "Independent Senators Group",
    "PSG": "Progressive Senate Group",
}

# Contact details come from the second table; when it can't be read these
# are left out of the rows (not blanked), so master.xlsx keeps what it has.
CONTACT_FIELDS = ("email", "phone")

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PRIVY_COUNCIL_RE = re.compile(r",?\s*P\.\s*C\.?$")
_NOT_PROFILE_LINKS = ("mailto:", "tel:", "#", "javascript:")


def _column(keys, word):
    """Index of the first heading (column_key() form) containing `word`, or None."""
    return next((i for i, key in enumerate(keys) if word in key), None)


def _cell(row, index):
    return row[index] if index is not None and index < len(row) else ""


def split_name(listed):
    """The Senate lists "Last, First": "Adler, Charles S." -> ("Charles S.",
    "Adler"). A Privy Councillor's ", P.C." is dropped."""
    last, _, first = listed.partition(",")
    first = _PRIVY_COUNCIL_RE.sub("", first.strip()).strip(" ,")
    return first, last.strip()


def _name_key(listed):
    """How the two tables' names are paired when their links don't match:
    last name + first word of the first name, since the contact table can
    add a middle initial ("Surette, Allister W.") or drop ", P.C."."""
    first, last = split_name(listed)
    first_words = normalize_name(first).split()
    return normalize_name(last), first_words[0] if first_words else ""


def _profile_url(row, base):
    """The first link in a table row that isn't an email address or the
    like, made absolute ("" if there isn't one)."""
    href = next((h for h in row.links if not h.lower().startswith(_NOT_PROFILE_LINKS)), "")
    return urllib.parse.urljoin(base, href) if href else ""


def _url_key(url):
    return urllib.parse.urlsplit(url).path.rstrip("/").lower()


def parse_list(page_text):
    """
    The List tab's senators, each a dict: name (as listed, "Last, First"),
    url (their profile), affiliation (as listed, e.g. "ISG"), province,
    appointed, retirement (ISO dates) and advised_by. Raises ValueError if
    the table isn't there or lists nobody.
    """
    found = find_table(html_tables(page_text), ("name", "affiliation"))
    if not found:
        raise ValueError("the list of senators has no Name / Affiliation table any more -- "
                         "the Senate may have changed its website")
    keys, rows = found
    columns = {
        "name": keys.index("name"),
        "affiliation": keys.index("affiliation"),
        "province": _column(keys, "province"),
        "appointed": _column(keys, "nomination"),
        "retirement": _column(keys, "retirement"),
        "advised_by": _column(keys, "advice"),
    }
    senators = []
    for row in rows:
        entry = {field: _cell(row, index) for field, index in columns.items()}
        if not entry["name"] or not entry["affiliation"]:
            continue  # not a senator's row (a note, a group heading...)
        for field in ("appointed", "retirement"):
            date = _DATE_RE.search(entry[field])  # drops footnote marks: "2011-05-25 1"
            entry[field] = date.group(0) if date else ""
        entry["url"] = _profile_url(row, LIST_URL)
        senators.append(entry)
    if not senators:
        raise ValueError("the list of senators came back empty")
    return senators


def parse_contacts(page_text):
    """
    The Contact Information tab as (by profile URL, by name), two dicts of
    {"phone": ..., "email": ...}; see _url_key() and _name_key(). A name
    shared by two senators is left out of the second. Raises ValueError if
    the table isn't there.
    """
    found = find_table(html_tables(page_text), ("phone", "email"))
    if not found:
        raise ValueError("the senators' contact list has no Phone / Email table any more")
    keys, rows = found
    name_column = next((i for i in (_column(keys, "senator"), _column(keys, "name")) if i is not None), 0)
    phone_column, email_column = keys.index("phone"), keys.index("email")
    by_url, by_name, names = {}, {}, Counter()
    for row in rows:
        name = _cell(row, name_column)
        if not name:
            continue
        email = _EMAIL_RE.search(_cell(row, email_column))
        if email:
            email = email.group(0)
        else:
            email = next((h[len("mailto:"):].split("?")[0] for h in row.links if h.lower().startswith("mailto:")), "")
        contact = {"phone": format_phone(_cell(row, phone_column)), "email": email}
        url = _profile_url(row, CONTACTS_URL)
        if url:
            by_url[_url_key(url)] = contact
        key = _name_key(name)
        names[key] += 1
        by_name[key] = contact
    for key, count in names.items():
        if count > 1:
            del by_name[key]
    return by_url, by_name


def senator_row(entry, contacts=None):
    """One parse_list() entry as a flat row (CSV_FIELDS), with phone and
    email from parse_contacts()'s result -- or, when `contacts` is None,
    without those two keys at all."""
    first, last = split_name(entry["name"])
    affiliation = entry["affiliation"]
    extra = (("appointed", entry["appointed"]), ("retirement", entry["retirement"]),
             ("appointed_on_advice_of", entry["advised_by"]))
    row = {field: "" for field in CSV_FIELDS if field not in CONTACT_FIELDS}
    row.update({
        "category": "federal",
        "representative_set": SET_NAME,
        "name": " ".join(part for part in (first, last) if part),
        "first_name": first,
        "last_name": last,
        "elected_office": OFFICE,
        "district_name": entry["province"],
        "party_name": AFFILIATIONS.get(affiliation, affiliation),
        "postal_address": POSTAL_ADDRESS,
        "url": entry["url"],
        "source_url": SENATORS_PAGE,
        "province_territory": province_code(entry["province"]),
        "extra_info": "; ".join(f"{key}: {value}" for key, value in extra if value),
    })
    if contacts is not None:
        by_url, by_name = contacts
        contact = (entry["url"] and by_url.get(_url_key(entry["url"]))) or by_name.get(_name_key(entry["name"])) or {}
        row.update({field: contact.get(field, "") for field in CONTACT_FIELDS})
    return row


def fetch_senators():
    """
    Every sitting senator as a row for master.xlsx. Raises FetchError or
    ValueError if the List can't be read. If only the contact table
    fails, the rows come without phone and email (see senator_row()) and
    a warning is printed.
    """
    print("Fetching the Senate's list of senators (sencanada.ca)...")
    listed = parse_list(http_get_html(LIST_URL))
    try:
        contacts = parse_contacts(http_get_html(CONTACTS_URL))
    except (FetchError, ValueError) as e:
        print(f"  ! Couldn't read the senators' phone numbers and emails ({e}) -- "
              f"keeping the ones already in master.xlsx.")
        contacts = None
    rows = [senator_row(entry, contacts) for entry in listed]
    if contacts is not None:
        missing = [r["name"] for r in rows if not r["phone"] and not r["email"]]
        if missing:
            print(f"  (no contact details published yet for: {', '.join(missing)})")
    print(f"  -> {SET_NAME}: {len(rows)} senator(s)")
    return rows
