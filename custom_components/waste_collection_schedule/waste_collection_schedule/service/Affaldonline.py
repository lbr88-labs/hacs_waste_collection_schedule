"""Affaldonline calendar platform (several Danish municipalities).

The address page (``showInfo.php``) normally prints only the next emptying.
Municipalities that also link ``showToemCal.php`` publish a full-year calendar
PDF. That PDF is an icon grid: the waste type is a small image next to the
day, not text, so :class:`~waste_collection_schedule.parsers.PdfTextParser`
and :class:`~waste_collection_schedule.parsers.PdfTableParser` cannot read it.
This module walks the PDF layout, matches each icon to the legend, and yields
``(date, label)`` rows.

When the PDF is missing or does not match the address page, the caller falls
back to the HTML parser for that municipality (the old "Næste tømningsdag"
line, Silkeborg's date table, or Favrskov's per-fraction lines).
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs

from bs4 import BeautifulSoup

from waste_collection_schedule.exceptions import (
    SourceArgAmbiguousWithSuggestions,
    SourceArgumentNotFoundWithSuggestions,
)
from waste_collection_schedule.retrievers import Request

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.affaldonline.dk/kalender/{municipality}"

# ``fallback`` is the address-page parser used when the year PDF cannot be
# read. ``pdf`` means that page has no HTML schedule of its own.
MUNICIPALITIES: dict[str, dict[str, str]] = {
    "aeroe": {
        "title": "Ærø Kommune",
        "url": "https://www.aeroekommune.dk/",
        "fallback": "next",
    },
    "assens": {
        "title": "Assens Forsyning",
        "url": "https://www.assensforsyning.dk/",
        "fallback": "next",
    },
    "favrskov": {
        "title": "Favrskov Forsyning",
        "url": "https://www.favrskovforsyning.dk",
        "fallback": "fractions",
    },
    "fanoe": {
        "title": "Fanø Kommune",
        "url": "https://fanoe.dk/",
        "fallback": "pdf",
    },
    "ffv": {
        "title": "Faaborg Forsynings Virksomhed",
        "url": "https://www.ffv.dk/",
        "fallback": "pdf",
    },
    "fredericia": {
        "title": "Fredericia Kommune Affald & Genbrug",
        "url": "https://affaldgenbrug-fredericia.dk/",
        "fallback": "pdf",
    },
    "holbaek": {
        "title": "Fors A/S (Holbæk Kommune)",
        "url": "https://www.fors.dk/affald/afhentning-af-affald/",
        "fallback": "next",
    },
    "langeland": {
        "title": "Langeland Forsyning",
        "url": "https://www.langeland-forsyning.dk/",
        "fallback": "next",
    },
    "middelfart": {
        "title": "Middelfart Kommune",
        "url": "https://middelfart.dk/",
        "fallback": "next",
    },
    "nyborg": {
        "title": "Nyborg Forsyning & Service A/S",
        "url": "https://www.nfs.as/",
        "fallback": "pdf",
    },
    "rebild": {
        "title": "Rebild Kommune",
        "url": "https://rebild.dk/",
        "fallback": "next",
    },
    "silkeborg": {
        "title": "Silkeborg Forsyning",
        "url": "https://www.silkeborgforsyning.dk/",
        "fallback": "table",
    },
    "soroe": {
        "title": "Sorø Kommune",
        "url": "https://soroe.dk/",
        "fallback": "pdf",
    },
    "vejle": {
        "title": "Vejle Kommune",
        "url": "https://www.vejle.dk/",
        "fallback": "next",
    },
    "viborg": {
        "title": "Revas (Viborg Kommune)",
        "url": "https://www.revas.dk/",
        "fallback": "next",
    },
}

DANISH_MONTHS = [
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
]
_MONTH_INDEX = {
    name.upper(): index for index, name in enumerate(DANISH_MONTHS, start=1)
}
_CALENDAR_HREF_RE = re.compile(r"showToemCal\.php\?([^'\"\s<>]+)", re.IGNORECASE)
_CALENDAR_YEAR_RE = re.compile(
    r"(?:Affalds|Tømnings|Tømme|Tomnings|Tomme)?kalender\s+(\d{4})",
    re.IGNORECASE,
)
_DATE_LINE_RE = re.compile(
    r"^(man|tir|ons|tor|fre|lør|søn|lor|son)\s*(\d{1,2})$",
    re.IGNORECASE,
)
_WEEKDAYS = {"man", "tir", "ons", "tor", "fre", "lør", "søn", "lor", "son"}
_LEGEND_SKIP_PREFIXES = (
    "affaldskalender",
    "tømningskalender",
    "tømmekalender",
    "tomningskalender",
    "tommekalender",
    "tømningsadresse",
    "tomningsadresse",
    "husk at",
    "printer du",
    "f.eks.",
    "at have",
    "beskeder",
    "via vores",
    "log ind",
    "dine beholdere",
    "gældende fra",
    "hent appen",
    "hent vores",
    "hvis din",
    "der kunne ikke",
)
_MAX_LEGEND_LABEL_LENGTH = 80
_ROW_CENTER_TOLERANCE = 6.0
_LEGEND_CENTER_TOLERANCE = 8.0
_ICON_GROUP_GAP = 24.0
_DAY_NUMBER_GAP = 30.0
_COLUMN_GAP = 20.0
_MAX_ICON_SIZE = 40.0

_VALUE_CACHE: dict[tuple[str, str, str, str | None, str | None], str] = {}

_SHOW_INFO = Request(
    lambda resolved, municipality, **_: (
        f"https://www.affaldonline.dk/kalender/{municipality}/showInfo.php"
    ),
    method="POST",
    data=lambda resolved, **_: {"values": resolved},
    raise_for_status=True,
)
_STREETS = Request(
    lambda municipality, **_: (
        f"https://www.affaldonline.dk/kalender/{municipality}/acCal.php"
    ),
    params=lambda street, **_: {"term": street or ""},
    raise_for_status=True,
)
_HOUSES = Request(
    lambda street_row, municipality, **_: (
        f"https://www.affaldonline.dk/kalender/{municipality}/husnrCal.php"
    ),
    params=lambda street_row, **_: {
        "vejnavn": street_row["vejnavn"],
        "postnr": street_row["postnr"],
        "postdist": street_row["Bynavn"],
    },
    raise_for_status=True,
)
_CALENDAR_PDF = Request(
    lambda base_url, query, **_: f"{base_url}/showToemCal.php",
    params=lambda base_url, query, **_: query,
    raise_for_status=True,
)


def clear_value_cache() -> None:
    """Drop cached address lookups. Tests use this between cases."""
    _VALUE_CACHE.clear()


def _cache_key(params: dict[str, Any]) -> tuple[str, str, str, str | None, str | None]:
    return (
        str(params.get("municipality") or ""),
        str(params.get("street") or ""),
        str(params.get("house_number") or ""),
        params.get("postal_code"),
        params.get("city"),
    )


def _display_street(street: dict) -> str:
    return str(
        street.get("value")
        or f"{street.get('vejnavn', '')} ({street.get('postnr', '')} {street.get('Bynavn', '')})"
    )


def _normalize_house_number(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _pick_street(response: Any, **params: Any) -> dict:
    street = str(params.get("street") or "")
    postal_code = params.get("postal_code")
    city = params.get("city")
    candidates = response.json() or []
    matching = []
    suggestions = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        suggestions.append(_display_street(candidate))
        if str(candidate.get("vejnavn", "")).casefold() == street.casefold():
            matching.append(candidate)
    if not matching:
        raise SourceArgumentNotFoundWithSuggestions("street", street, suggestions)
    if postal_code is not None:
        matching = [
            candidate
            for candidate in matching
            if str(candidate.get("postnr", "")).strip() == str(postal_code)
        ]
        if not matching:
            raise SourceArgumentNotFoundWithSuggestions("street", street, suggestions)
    if city is not None:
        city_matches = [
            candidate
            for candidate in matching
            if str(candidate.get("Bynavn", "")).casefold() == str(city).casefold()
        ]
        if city_matches:
            matching = city_matches
    if len(matching) > 1:
        raise SourceArgAmbiguousWithSuggestions(
            "street",
            street,
            [_display_street(candidate) for candidate in matching],
        )
    return matching[0]


def _pick_house(response: Any, street_row: dict, **params: Any) -> str:
    house_number = str(params.get("house_number") or "")
    soup = BeautifulSoup(response.text, "html.parser")
    options = soup.find_all("option")
    suggestions = [option.get_text(strip=True) for option in options]
    wanted = _normalize_house_number(house_number)
    for option in options:
        label = option.get_text(strip=True)
        if (
            label.casefold() == house_number.casefold()
            or _normalize_house_number(label) == wanted
        ):
            return str(option["value"])
    raise SourceArgumentNotFoundWithSuggestions(
        "house_number", house_number, suggestions
    )


def resolve_values(source: Any) -> str:
    """The Affaldonline ``values`` string, from the user or an address lookup."""
    params = source.params
    supplied = params.get("values")
    if supplied:
        return str(supplied)
    key = _cache_key(params)
    cached = _VALUE_CACHE.get(key)
    if cached is not None:
        return cached
    street_row = _pick_street(_STREETS(source), **params)
    values = _pick_house(_HOUSES(source, street_row), street_row, **params)
    _VALUE_CACHE[key] = values
    return values


class AffaldonlinePage:
    """POST the address page. Resolves street and house number when needed."""

    def __call__(self, source: Any) -> Any:
        municipality = source.params.get("municipality")
        if municipality not in MUNICIPALITIES:
            raise SourceArgumentNotFoundWithSuggestions(
                "municipality",
                municipality,
                list(MUNICIPALITIES),
            )
        return _SHOW_INFO(source, resolve_values(source))


def _calendar_buttons(soup: BeautifulSoup) -> list[dict[str, str]]:
    blobs = [str(soup)]
    for tag in soup.find_all(onclick=True):
        onclick = tag.get("onclick")
        if onclick:
            blobs.append(str(onclick))
    found: list[dict[str, str]] = []
    seen_years: set[str] = set()
    for blob in blobs:
        for match in _CALENDAR_HREF_RE.finditer(blob.replace("&amp;", "&")):
            query = parse_qs(match.group(1), keep_blank_values=True)
            button = {key: values[0] for key, values in query.items() if values}
            year = button.get("year", "")
            if not year.isdigit() or year in seen_years:
                continue
            seen_years.add(year)
            found.append(button)
    found.sort(key=lambda button: int(button["year"]))
    return found


def _following_year_button(buttons: list[dict[str, str]]) -> dict[str, str] | None:
    if not buttons:
        return None
    latest = max(buttons, key=lambda button: int(button["year"]))
    next_year = str(int(latest["year"]) + 1)
    if any(button["year"] == next_year for button in buttons):
        return None
    return {**latest, "year": next_year}


def _download_pdf(
    source: Any, base_url: str, query: dict[str, str], *, quiet: bool = False
) -> bytes | None:
    """Download one year calendar. ``quiet`` is for a year that may not exist yet."""
    log = _LOGGER.debug if quiet else _LOGGER.warning
    try:
        response = _CALENDAR_PDF(source, base_url, query)
    except Exception as error:
        log(
            "Affaldonline calendar for %s could not be read: %s",
            query.get("year"),
            error,
        )
        return None
    if not response.content.startswith(b"%PDF"):
        log("Affaldonline calendar for %s was not a PDF", query.get("year"))
        return None
    return response.content


class AffaldonlineCalendars:
    """Download every published year calendar, plus the following year."""

    def __call__(self, source: Any, page: Any) -> dict[str, Any]:
        soup = BeautifulSoup(page.text, "html.parser")
        buttons = _calendar_buttons(soup)
        if not buttons:
            raise LookupError("Affaldonline page has no year calendar")
        page_url = str(getattr(page, "url", "") or "")
        base_url = page_url.rsplit("/", 1)[0] if page_url else ""
        if not base_url:
            base_url = BASE_URL.format(municipality=source.params["municipality"])
        years = []
        for button in buttons:
            content = _download_pdf(source, base_url, button)
            if content is not None:
                years.append((button["year"], content))
        following = None
        following_button = _following_year_button(buttons)
        if following_button is not None:
            content = _download_pdf(source, base_url, following_button, quiet=True)
            if content is not None:
                following = (following_button["year"], content)
        return {"html": page.text, "years": years, "following": following}


def _y_center(bbox: tuple[float, float, float, float]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _same_row(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    return abs(_y_center(first) - _y_center(second)) <= tolerance


def _calendar_page_items(page: Any) -> tuple[list[dict], list[dict]]:
    from pdfminer.layout import LTImage, LTTextLine

    texts: list[dict] = []
    images: list[dict] = []
    seen_text: set[tuple[str, float, float]] = set()
    seen_images: set[tuple[float, float, str]] = set()

    def walk(element: Any) -> None:
        if isinstance(element, LTTextLine):
            text = element.get_text().strip()
            if not text:
                return
            text_key = (text, round(element.bbox[0], 1), round(element.bbox[1], 1))
            if text_key in seen_text:
                return
            seen_text.add(text_key)
            texts.append({"text": text, "bbox": tuple(element.bbox)})
            return
        if isinstance(element, LTImage):
            if element.width > _MAX_ICON_SIZE or element.height > _MAX_ICON_SIZE:
                return
            stream = element.stream
            if stream is None:
                return
            try:
                data = stream.get_data()
            except Exception:
                _LOGGER.debug("Skipping unreadable Affaldonline calendar icon")
                return
            if not data:
                return
            digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
            image_key = (round(element.bbox[0], 1), round(element.bbox[1], 1), digest)
            if image_key in seen_images:
                return
            seen_images.add(image_key)
            images.append({"hash": digest, "bbox": tuple(element.bbox)})
            return
        if hasattr(element, "__iter__"):
            for child in element:
                walk(child)

    walk(page)
    return texts, images


def _calendar_year(texts: list[dict], fallback_year: int | None) -> int | None:
    for item in texts:
        match = _CALENDAR_YEAR_RE.search(item["text"])
        if match:
            return int(match.group(1))
    return fallback_year


def _month_headers(texts: list[dict]) -> list[dict]:
    headers = []
    for item in texts:
        month = _MONTH_INDEX.get(item["text"].upper())
        if month:
            headers.append({"month": month, "bbox": item["bbox"]})
    return headers


def _is_legend_label(text: str) -> bool:
    lowered = text.casefold()
    if len(text) > _MAX_LEGEND_LABEL_LENGTH or lowered.startswith(
        _LEGEND_SKIP_PREFIXES
    ):
        return False
    if text.upper() in _MONTH_INDEX or _DATE_LINE_RE.match(text):
        return False
    if lowered in _WEEKDAYS or re.fullmatch(r"\d{1,2}", text):
        return False
    return True


def _legend_sequences(texts: list[dict], images: list[dict]) -> list[tuple]:
    entries = []
    for item in texts:
        if not _is_legend_label(item["text"]):
            continue
        bbox = item["bbox"]
        candidates = [
            image
            for image in images
            if _same_row(image["bbox"], bbox, _LEGEND_CENTER_TOLERANCE)
            and image["bbox"][2] <= bbox[0] + 2
        ]
        candidates.sort(key=lambda image: image["bbox"][0])
        group = []
        previous_left = bbox[0]
        for image in reversed(candidates):
            if previous_left - image["bbox"][2] > _ICON_GROUP_GAP:
                break
            group.append(image)
            previous_left = image["bbox"][0]
        group.reverse()
        if group:
            entries.append((tuple(image["hash"] for image in group), item["text"]))
    return entries


def _calendar_date_rows(texts: list[dict]) -> list[dict]:
    fragments: list[tuple[str, int | None, Any]] = []
    for item in texts:
        text = item["text"]
        match = _DATE_LINE_RE.match(text)
        if match:
            fragments.append(("full", int(match.group(2)), item["bbox"]))
            continue
        if text.casefold() in _WEEKDAYS:
            fragments.append(("weekday", None, item["bbox"]))
            continue
        if re.fullmatch(r"\d{1,2}", text):
            fragments.append(("day", int(text), item["bbox"]))

    rows = []
    used: set[int] = set()
    for index, fragment in enumerate(fragments):
        kind, day, bbox = fragment
        if kind != "full":
            continue
        rows.append({"day": day, "bbox": bbox, "x": bbox[0]})
        used.add(index)

    for index, fragment in enumerate(fragments):
        kind, _day, bbox = fragment
        if index in used or kind != "weekday":
            continue
        day_number = None
        day_bbox = None
        day_index = None
        for other_index, other in enumerate(fragments):
            other_kind, other_day, other_bbox = other
            if other_index in used or other_kind != "day":
                continue
            if not _same_row(bbox, other_bbox, 3):
                continue
            if other_bbox[0] < bbox[0] - 1:
                continue
            if other_bbox[0] - bbox[2] > _DAY_NUMBER_GAP:
                continue
            day_number = other_day
            day_bbox = other_bbox
            day_index = other_index
            break
        if day_number is None or day_bbox is None or day_index is None:
            continue
        used.add(index)
        used.add(day_index)
        rows.append(
            {
                "day": day_number,
                "bbox": (
                    min(bbox[0], day_bbox[0]),
                    min(bbox[1], day_bbox[1]),
                    max(bbox[2], day_bbox[2]),
                    max(bbox[3], day_bbox[3]),
                ),
                "x": bbox[0],
            }
        )
    return rows


def _month_header_for_row(row: dict, headers: list[dict]) -> dict | None:
    row_y = _y_center(row["bbox"])
    above = [header for header in headers if header["bbox"][1] > row_y - 1]
    if not above:
        return None
    nearest_y = min(header["bbox"][1] for header in above)
    same_band = [header for header in above if abs(header["bbox"][1] - nearest_y) < 8]
    return min(same_band, key=lambda header: abs(header["bbox"][0] - row["x"]))


def _row_icon_sequence(row: dict, images: list[dict], rows: list[dict]) -> tuple:
    right_edge = 10000.0
    for other in rows:
        if other["x"] > row["x"] + _COLUMN_GAP:
            right_edge = min(right_edge, other["x"])
    icons = [
        image
        for image in images
        if _same_row(image["bbox"], row["bbox"], _ROW_CENTER_TOLERANCE)
        and row["bbox"][2] - 1 <= image["bbox"][0] < right_edge - 1
    ]
    icons.sort(key=lambda image: image["bbox"][0])
    return tuple(image["hash"] for image in icons)


def _match_icon_sequence(icons: tuple, legend: list[tuple]) -> list[str] | None:
    if not icons:
        return None
    labels = []
    index = 0
    while index < len(icons):
        matched = False
        for sequence, label in legend:
            size = len(sequence)
            if icons[index : index + size] == sequence:
                labels.append(label)
                index += size
                matched = True
                break
        if not matched:
            return None
    return labels


def _parse_calendar_pdf(
    pdf_bytes: bytes, fallback_year: int | None = None
) -> tuple[list[tuple[date, str]], int]:
    from io import BytesIO

    from pdfminer.high_level import extract_pages

    pages: list[dict[str, Any]] = []
    for pdf_page in extract_pages(BytesIO(pdf_bytes)):
        texts, images = _calendar_page_items(pdf_page)
        pages.append(
            {
                "texts": texts,
                "images": images,
                "year": _calendar_year(texts, fallback_year),
                "headers": _month_headers(texts),
                "rows": _calendar_date_rows(texts),
            }
        )

    legend: dict[tuple[str, ...], str] = {}
    for page in pages:
        for sequence, label in _legend_sequences(page["texts"], page["images"]):
            legend.setdefault(sequence, label)
    legend_sorted = sorted(legend.items(), key=lambda item: len(item[0]), reverse=True)

    entries: list[tuple[date, str]] = []
    unmatched = 0
    for page in pages:
        if not page["year"] or not page["headers"]:
            unmatched += len(page["rows"])
            continue
        for row in page["rows"]:
            header = _month_header_for_row(row, page["headers"])
            labels = None
            if header is not None:
                labels = _match_icon_sequence(
                    _row_icon_sequence(row, page["images"], page["rows"]),
                    legend_sorted,
                )
            if header is None or not labels:
                unmatched += 1
                continue
            try:
                collection_date = date(page["year"], header["month"], row["day"])
            except ValueError:
                unmatched += 1
                _LOGGER.warning(
                    "Ignoring invalid Affaldonline calendar date %s-%s-%s",
                    page["year"],
                    header["month"],
                    row["day"],
                )
                continue
            entries.extend((collection_date, label) for label in labels)
    if unmatched:
        _LOGGER.warning(
            "Affaldonline calendar PDF left %s collection row(s) unmatched",
            unmatched,
        )
    return entries, unmatched


def _next_emptying_dates(soup: BeautifulSoup) -> set[date]:
    dates: set[date] = set()
    text = soup.get_text(" ", strip=True)
    for match in re.finditer(
        r"Næste tømningsdag\D{0,40}(\d{1,2})\.\s*(\w+)\s+(\d{4})",
        text,
        re.IGNORECASE,
    ):
        month_name = match.group(2).casefold()
        if month_name not in DANISH_MONTHS:
            continue
        try:
            dates.add(
                date(
                    int(match.group(3)),
                    DANISH_MONTHS.index(month_name) + 1,
                    int(match.group(1)),
                )
            )
        except ValueError:
            continue
    return dates


def _calendar_is_usable(
    entries: list[tuple[date, str]],
    unmatched: int,
    expected_dates: set[date] | None,
) -> bool:
    if unmatched or not entries:
        return False
    if any(_DATE_LINE_RE.match(label) for _day, label in entries):
        return False
    dates = {day for day, _label in entries}
    if len(dates) < 4:
        return False
    if expected_dates and not expected_dates.issubset(dates):
        return False
    return True


def _unique(entries: list[tuple[date, str]]) -> list[tuple[date, str]]:
    seen: set[tuple[date, str]] = set()
    unique: list[tuple[date, str]] = []
    for item in sorted(entries):
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


class AffaldonlineCalendarParser:
    """Turn downloaded year PDFs into ``(date, label)`` rows when they match."""

    def __call__(self, response: Any, source: Any = None) -> list[tuple[date, str]]:
        if not response or not response.get("years"):
            return []
        soup = BeautifulSoup(response["html"], "html.parser")
        expected = _next_emptying_dates(soup)
        entries: list[tuple[date, str]] = []
        parsed_a_year = False
        for year, content in response["years"]:
            try:
                parsed, unmatched = _parse_calendar_pdf(content, int(year))
            except (OSError, ValueError) as error:
                _LOGGER.warning(
                    "Affaldonline calendar for %s could not be read: %s",
                    year,
                    error,
                )
                continue
            if not _calendar_is_usable(parsed, unmatched, expected):
                _LOGGER.warning("Ignoring Affaldonline calendar %s", year)
                continue
            entries.extend(parsed)
            parsed_a_year = True
        if not parsed_a_year:
            return []
        following = response.get("following")
        if following is not None:
            year, content = following
            try:
                parsed, unmatched = _parse_calendar_pdf(content, int(year))
            except (OSError, ValueError):
                _LOGGER.debug("No Affaldonline calendar published for %s", year)
            else:
                if _calendar_is_usable(parsed, unmatched, None):
                    entries.extend(parsed)
        return _unique(entries)


def _parse_next(soup: BeautifulSoup) -> list[tuple[date, str]]:
    entries: list[tuple[date, str]] = []
    next_pickup_info = soup.find_all(string=re.compile("Næste tømningsdag:"))
    if not next_pickup_info:
        raise ValueError("No waste schemes found. Please check the provided values.")
    for info in next_pickup_info:
        text = info.strip()
        match = re.search(r"(\d{1,2})\. (\w+) (\d{4})", text)
        if not match:
            _LOGGER.warning("No valid date found in string: %s", text)
            continue
        try:
            day = int(match.group(1))
            month_index = DANISH_MONTHS.index(match.group(2)) + 1
            year = int(match.group(3))
            formatted = date(year, month_index, day)
        except ValueError as error:
            _LOGGER.error("Error parsing date: %s from string: %s", error, text)
            continue
        waste_type_search = re.search(r"\((.*?)\)", text)
        if waste_type_search is None:
            _LOGGER.warning("No waste type found in string: %s", text)
            continue
        for waste_type in waste_type_search.group(1).split(","):
            entries.append((formatted, waste_type.strip()))
    return entries


def _parse_table(soup: BeautifulSoup) -> list[tuple[date, str]]:
    entries: list[tuple[date, str]] = []
    table = soup.find("table")
    if not table:
        raise ValueError(
            "No waste collection table found. Please check the provided values."
        )
    current_year = datetime.now().year
    current_month = datetime.now().month
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        match = re.search(r"(\d{2})-(\d{2})", cells[0].get_text(strip=True))
        if not match:
            continue
        day = int(match.group(1))
        month = int(match.group(2))
        collection_year = current_year + (1 if month < current_month else 0)
        collection_date = date(collection_year, month, day)
        for waste_type in cells[1].get_text(strip=True).split(","):
            entries.append((collection_date, waste_type.strip()))
    return entries


def _parse_fractions(soup: BeautifulSoup) -> list[tuple[date, str]]:
    entries: list[tuple[date, str]] = []
    strong_tags = soup.find_all("strong")
    if not strong_tags:
        raise ValueError("No waste schemes found. Please check the provided values.")
    for strong_tag in strong_tags:
        waste_type = strong_tag.get_text(strip=True)
        next_sibling = strong_tag.find_next_sibling(string=True)
        if not next_sibling or "Næste tømningsdag" not in next_sibling:
            continue
        match = re.search(r"(\d{1,2})\. (\w+) (\d{4})", next_sibling)
        if not match:
            _LOGGER.warning("No valid date found in string: %s", next_sibling)
            continue
        try:
            formatted = date(
                int(match.group(3)),
                DANISH_MONTHS.index(match.group(2)) + 1,
                int(match.group(1)),
            )
        except ValueError as error:
            _LOGGER.error("Error parsing date: %s from string: %s", error, next_sibling)
            continue
        entries.append((formatted, waste_type))
    return entries


_HTML_PARSERS = {
    "next": _parse_next,
    "table": _parse_table,
    "fractions": _parse_fractions,
}


class AffaldonlineHtmlParser:
    """The address-page schedule, used only when the year PDF cannot be read."""

    def __call__(self, response: Any, source: Any = None) -> list[tuple[date, str]]:
        municipality = "" if source is None else source.params.get("municipality")
        fallback = MUNICIPALITIES.get(str(municipality), {}).get("fallback", "next")
        if fallback == "pdf":
            raise ValueError(
                "No waste collection dates found in the Affaldonline calendar. "
                "Please check the provided values."
            )
        soup = BeautifulSoup(response.text, "html.parser")
        entries = _HTML_PARSERS[fallback](soup)
        if not entries:
            raise ValueError(
                "No waste schemes found. Please check the provided values."
            )
        return entries
