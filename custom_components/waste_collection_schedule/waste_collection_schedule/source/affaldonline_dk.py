import hashlib
import logging
import random
import re
from datetime import date, datetime
from functools import lru_cache
from io import BytesIO
from typing import List
from urllib.parse import parse_qs

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage, LTTextLine
from waste_collection_schedule import Collection  # type: ignore[attr-defined]
from waste_collection_schedule.exceptions import (
    SourceArgAmbiguousWithSuggestions,
    SourceArgumentNotFoundWithSuggestions,
    SourceArgumentRequired,
)

TITLE = "Affaldonline"
DESCRIPTION = "Affaldonline"
URL = "https://affaldonline.dk"
BASE_URL = "https://www.affaldonline.dk/kalender/{municipality}"
API_URL = "https://www.affaldonline.dk/kalender/{municipality}/showInfo.php"

_LOGGER = logging.getLogger("waste_collection_schedule.affaldonline_dk")

PARSERS = {
    "default": {
        "description": "Næste tømningsdag: DD den D MMMMM YYYY (waste_type_1, waste_type_2)",
        "regex": r"(\d{1,2})\. (\w+) (\d{4})",
        "enabled": True,
    },
    "silkeborg": {
        "description": "A table with dates in the format DD-MM and waste types",
        "regex": r"(\d{2})-(\d{2})",
        "enabled": True,
    },
    "favrskov": {
        "description": "Blåmejsevej 1 (8382 Hinnerup) with multiple waste types",
        "regex": r"Næste tømningsdag: (\w+) den (\d{1,2})\. (\w+) (\d{4})",
        "enabled": True,
    },
    "pdf": {
        "description": "Full-year calendar PDF linked from the address result",
        "regex": None,
        "enabled": True,
    },
}

AFFALDONLINE_MUNICIPALITIES = {
    "aeroe": {
        "title": "Ærø Kommune",
        "url": "https://www.aeroekommune.dk/",
        "parser": "default",
        "values": "Nørregade|1||||5970|Ærøskøbing|1228262|448776|0",
    },
    "assens": {
        "title": "Assens Forsyning",
        "url": "https://www.assensforsyning.dk/",
        "parser": "default",
        "values": "Nørregade|1||||5610|Assens|10894|430000|0",
    },
    "favrskov": {
        "title": "Favrskov Forsyning",
        "url": "https://www.favrskovforsyning.dk",
        "parser": "favrskov",
        "values": "Nørregade|1||||8382|Hinnerup|6443|108156|0",
    },
    "fanoe": {
        "title": "Fanø Kommune",
        "url": "https://fanoe.dk/",
        "parser": "pdf",
        "values": "Nørre Klit|5||||6720|Fanø|2582|1747246|0",
    },
    "ffv": {
        "title": "Faaborg Forsynings Virksomhed",
        "url": "https://www.ffv.dk/",
        "parser": "pdf",
        "values": "Marsk Billesvej|18||||5672|Broby|36193544|576846|0",
    },
    "fredericia": {
        "title": "Fredericia Kommune Affald & Genbrug",
        "url": "https://affaldgenbrug-fredericia.dk/",
        "parser": "pdf",
        "values": "Nørre Allé|5||||7000|Fredericia|11079971|1907927|0",
    },
    "holbaek": {
        "title": "Fors A/S (Holbæk Kommune)",
        "url": "https://www.fors.dk/affald/afhentning-af-affald/",
        "parser": "default",
        "values": "Tåstrup Møllevej|5||||4300|Holbæk|76490500|1676484|0",
    },
    "langeland": {
        "title": "Langeland Forsyning",
        "url": "https://www.langeland-forsyning.dk/",
        "parser": "default",
        "values": "Nørregade|1||||5900|Rudkøbing|3535|383566|0",
    },
    "middelfart": {
        "title": "Middelfart Kommune",
        "url": "https://middelfart.dk/",
        "parser": "default",
        "values": "Nørregade|2||||5592|Ejby|11288085|6496420|0",
    },
    "nyborg": {
        "title": "Nyborg Forsyning & Service A/S",
        "url": "https://www.nfs.as/",
        "parser": "pdf",
        "values": "Nørregade|5||||5800|Nyborg|8896288|552542|0",
    },
    "rebild": {
        "title": "Rebild Kommune",
        "url": "https://rebild.dk/",
        "parser": "default",
        "values": "Nørregade|1||||9500|Hobro|4676913|1012588|0",
    },
    "silkeborg": {
        "title": "Silkeborg Forsyning",
        "url": "https://www.silkeborgforsyning.dk/",
        "parser": "silkeborg",
        "values": "Nørregade|5||||8620|Kjellerup|45814316|1291964|0",
    },
    "soroe": {
        "title": "Sorø Kommune",
        "url": "https://soroe.dk/",
        "parser": "pdf",
        "values": "Nørrevej|4| |||4180|Sorø|8569|8838|0|0",
    },
    "vejle": {
        "title": "Vejle Kommune",
        "url": "https://www.vejle.dk/",
        "parser": "default",
        "values": "Nørregade|69||||7100|Vejle|16285351|16285351|0",
    },
}

EXTRA_INFO = [
    {
        "title": info["title"],
        "url": info["url"],
        "default_params": {"municipality": municipality},
    }
    for municipality, info in AFFALDONLINE_MUNICIPALITIES.items()
    if PARSERS[info["parser"]]["enabled"]
]


def select_test_cases(municipalities, mode="random_one_from_each_parser"):
    test_cases = {}
    parser_test_cases = {}

    for name, info in municipalities.items():
        parser = info["parser"]
        if PARSERS[parser]["enabled"]:
            if parser not in parser_test_cases:
                parser_test_cases[parser] = []
            parser_test_cases[parser].append((name, info))

    if mode == "random_one_from_each_parser":
        for parser, cases in parser_test_cases.items():
            selected_case = random.choice(cases)
            test_cases[selected_case[0]] = {
                "municipality": selected_case[0],
                "values": selected_case[1]["values"],
            }
    elif mode == "first_from_each_parser":
        for parser, cases in parser_test_cases.items():
            selected_case = cases[0]
            test_cases[selected_case[0]] = {
                "municipality": selected_case[0],
                "values": selected_case[1]["values"],
            }
    elif mode == "random_one":
        all_cases = [case for cases in parser_test_cases.values() for case in cases]
        selected_case = random.choice(all_cases)
        test_cases[selected_case[0]] = {
            "municipality": selected_case[0],
            "values": selected_case[1]["values"],
        }
    elif mode == "first_one":
        first_parser = list(parser_test_cases.keys())[0]
        selected_case = parser_test_cases[first_parser][0]
        test_cases[selected_case[0]] = {
            "municipality": selected_case[0],
            "values": selected_case[1]["values"],
        }
    elif mode == "all":
        for parser, cases in parser_test_cases.items():
            for case in cases:
                test_cases[case[0]] = {
                    "municipality": case[0],
                    "values": case[1]["values"],
                }

    return test_cases


# Dynamically generate TEST_CASES from the AFFALDONLINE_MUNICIPALITIES dictionary
TEST_CASES = select_test_cases(
    AFFALDONLINE_MUNICIPALITIES, mode="first_from_each_parser"
)

PARAM_TRANSLATIONS = {
    "en": {
        "municipality": "Municipality",
        "values": "Advanced values string",
        "street": "Street",
        "house_number": "House number",
        "postal_code": "Postal code",
        "city": "City",
    },
}

PARAM_DESCRIPTIONS = {
    "en": {
        "municipality": "AffaldOnline municipality key, for example 'holbaek'.",
        "values": (
            "Optional advanced AffaldOnline values string. If set, street and house "
            "number lookup is skipped."
        ),
        "street": "Street name. Required when values is not set.",
        "house_number": (
            "House number, including letter/floor/door if shown. Spaces are optional "
            "for compound labels. Required when values is not set."
        ),
        "postal_code": "Postal code. Recommended when a street name exists in multiple cities.",
        "city": (
            "City or postal district. Recommended when a street name exists in multiple "
            "cities; postal-code matches are still used if AffaldOnline has another "
            "district label."
        ),
    },
}

HOW_TO_GET_ARGUMENTS_DESCRIPTION = {
    "en": (
        "Use street, house_number, and preferably postal_code/city. The source will "
        "resolve the internal AffaldOnline values string automatically."
    ),
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
_CALENDAR_HREF_RE = re.compile(
    r"showToemCal\.php\?([^'\"\s<>]+)",
    re.IGNORECASE,
)
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


def _clean_optional(value: str | int | None) -> str | None:
    if value is None:
        return None

    return str(value).strip()


def _display_street_suggestion(street: dict) -> str:
    return str(
        street.get("value")
        or f"{street.get('vejnavn', '')} ({street.get('postnr', '')} {street.get('Bynavn', '')})"
    )


def _normalize_house_number_label(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


@lru_cache(maxsize=256)
def _resolve_values(
    municipality: str,
    street: str,
    house_number: str,
    postal_code: str | None,
    city: str | None,
) -> str:
    base_url = BASE_URL.format(municipality=municipality)
    street_response = requests.get(f"{base_url}/acCal.php", params={"term": street})
    street_response.raise_for_status()

    street_candidates = street_response.json() or []
    matching_streets = []
    suggestions = []

    for candidate in street_candidates:
        if not isinstance(candidate, dict):
            continue

        suggestion = _display_street_suggestion(candidate)
        suggestions.append(suggestion)

        candidate_street = str(candidate.get("vejnavn", "")).casefold()

        if candidate_street == street.casefold():
            matching_streets.append(candidate)

    if not matching_streets:
        raise SourceArgumentNotFoundWithSuggestions("street", street, suggestions)

    if postal_code is not None:
        matching_streets = [
            candidate
            for candidate in matching_streets
            if str(candidate.get("postnr", "")).strip() == postal_code
        ]

        if not matching_streets:
            raise SourceArgumentNotFoundWithSuggestions("street", street, suggestions)

    if city is not None:
        city_matches = [
            candidate
            for candidate in matching_streets
            if str(candidate.get("Bynavn", "")).casefold() == city.casefold()
        ]

        if city_matches:
            matching_streets = city_matches

    if len(matching_streets) > 1:
        raise SourceArgAmbiguousWithSuggestions(
            "street", street, [_display_street_suggestion(s) for s in matching_streets]
        )

    selected_street = matching_streets[0]
    house_response = requests.get(
        f"{base_url}/husnrCal.php",
        params={
            "vejnavn": selected_street["vejnavn"],
            "postnr": selected_street["postnr"],
            "postdist": selected_street["Bynavn"],
        },
    )
    house_response.raise_for_status()

    soup = BeautifulSoup(house_response.text, "html.parser")
    house_options = soup.find_all("option")
    house_suggestions = [option.get_text(strip=True) for option in house_options]
    normalized_house_number = _normalize_house_number_label(house_number)

    for option in house_options:
        option_text = option.get_text(strip=True)
        if (
            option_text.casefold() == house_number.casefold()
            or _normalize_house_number_label(option_text) == normalized_house_number
        ):
            return option["value"]

    raise SourceArgumentNotFoundWithSuggestions(
        "house_number", house_number, house_suggestions
    )


def _y_center(bbox: tuple[float, float, float, float]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _same_row(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    return abs(_y_center(first) - _y_center(second)) <= tolerance


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


def _calendar_page_items(page):
    texts = []
    images = []
    seen_text = set()
    seen_images = set()

    def walk(element):
        if isinstance(element, LTTextLine):
            text = element.get_text().strip()
            if not text:
                return
            key = (text, round(element.bbox[0], 1), round(element.bbox[1], 1))
            if key in seen_text:
                return
            seen_text.add(key)
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
            digest = hashlib.md5(data).hexdigest()
            key = (round(element.bbox[0], 1), round(element.bbox[1], 1), digest)
            if key in seen_images:
                return
            seen_images.add(key)
            images.append({"hash": digest, "bbox": tuple(element.bbox)})
            return

        if hasattr(element, "__iter__"):
            for child in element:
                walk(child)

    walk(page)
    return texts, images


def _calendar_year(texts, fallback_year: int | None) -> int | None:
    for item in texts:
        match = _CALENDAR_YEAR_RE.search(item["text"])
        if match:
            return int(match.group(1))
    return fallback_year


def _month_headers(texts):
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


def _legend_sequences(texts, images):
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


def _calendar_date_rows(texts):
    fragments = []
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


def _month_header_for_row(row, headers):
    row_y = _y_center(row["bbox"])
    above = [header for header in headers if header["bbox"][1] > row_y - 1]
    if not above:
        return None

    nearest_y = min(header["bbox"][1] for header in above)
    same_band = [header for header in above if abs(header["bbox"][1] - nearest_y) < 8]
    return min(same_band, key=lambda header: abs(header["bbox"][0] - row["x"]))


def _row_icon_sequence(row, images, rows):
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


def _match_icon_sequence(icons, legend):
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
) -> tuple[List[Collection], int]:
    pages = []
    for page in extract_pages(BytesIO(pdf_bytes)):
        texts, images = _calendar_page_items(page)
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

    entries: List[Collection] = []
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
            for label in labels:
                entries.append(Collection(date=collection_date, t=label))

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
    entries: List[Collection],
    unmatched: int,
    expected_dates: set[date] | None,
) -> bool:
    if unmatched or not entries:
        return False
    if any(_DATE_LINE_RE.match(entry.type) for entry in entries):
        return False

    dates = {entry.date for entry in entries}
    if len(dates) < 4:
        return False
    if expected_dates and not expected_dates.issubset(dates):
        return False
    return True


def _unique_collections(entries: List[Collection]) -> List[Collection]:
    seen: set[tuple[date, str]] = set()
    unique: List[Collection] = []
    for entry in sorted(entries, key=lambda item: (item.date, item.type)):
        key = (entry.date, entry.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


class Source:
    def __init__(
        self,
        municipality: str,
        values: str | None = None,
        street: str | None = None,
        house_number: str | int | None = None,
        postal_code: str | int | None = None,
        city: str | None = None,
    ):
        _LOGGER.debug(
            "Initializing Source with municipality=%s, values=%s, street=%s, house_number=%s, postal_code=%s, city=%s",
            municipality,
            values,
            street,
            house_number,
            postal_code,
            city,
        )
        self._api_url = API_URL.format(municipality=municipality)
        self._parser_type = AFFALDONLINE_MUNICIPALITIES.get(municipality, {}).get(
            "parser"
        )
        if not self._parser_type:
            raise SourceArgumentNotFoundWithSuggestions(
                "municipality", municipality, AFFALDONLINE_MUNICIPALITIES.keys()
            )

        parser = getattr(self, f"_parse_{self._parser_type}", None)
        if parser is None:
            raise ValueError(f"Parser method for {self._parser_type} not implemented")
        if not callable(parser):
            raise ValueError(f"Parser method for {self._parser_type} is not callable")

        street = _clean_optional(street)
        house_number = _clean_optional(house_number)
        postal_code = _clean_optional(postal_code)
        city = _clean_optional(city)

        if values is None:
            if street is None:
                raise SourceArgumentRequired(
                    "street", "provide either values or street + house_number"
                )
            if house_number is None:
                raise SourceArgumentRequired(
                    "house_number", "provide either values or street + house_number"
                )

            values = _resolve_values(
                municipality, street, house_number, postal_code, city
            )

        self._values = values
        self._parser_method = parser

    def fetch(self) -> List[Collection]:
        _LOGGER.debug("Fetching data from %s", self._api_url)

        entries: List[Collection] = []

        post_data = {"values": self._values}

        response = requests.post(self._api_url, data=post_data)
        response.raise_for_status()

        html_content = response.text
        soup = BeautifulSoup(html_content, "html.parser")

        calendar_entries = self._published_calendar(soup)
        if calendar_entries is not None:
            return calendar_entries

        if self._parser_type == "pdf":
            raise ValueError(
                "No waste collection dates found in the Affaldonline calendar. "
                "Please check the provided values."
            )

        entries.extend(self._parser_method(soup))

        return entries

    def _download_calendar_pdf(self, button: dict[str, str]) -> bytes:
        base_url = self._api_url.rsplit("/", 1)[0]
        response = requests.get(
            f"{base_url}/showToemCal.php",
            params=button,
            timeout=30,
        )
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(
                f"Affaldonline calendar for {button['year']} was not a PDF"
            )
        return response.content

    def _published_calendar(self, soup: BeautifulSoup) -> List[Collection] | None:
        """Return the full year calendar when its PDF can be read.

        The address page normally shows only the next emptying. Municipalities
        that also link showToemCal.php get every date from that PDF. A calendar
        that does not match the page, or that cannot be read, is left unused so
        the municipality's previous parser still runs.
        """
        buttons = _calendar_buttons(soup)
        if not buttons:
            return None

        expected_dates = _next_emptying_dates(soup)
        entries: List[Collection] = []
        parsed_a_year = False
        for button in buttons:
            try:
                parsed, unmatched = _parse_calendar_pdf(
                    self._download_calendar_pdf(button),
                    fallback_year=int(button["year"]),
                )
            except (requests.RequestException, ValueError) as error:
                _LOGGER.warning(
                    "Affaldonline calendar for %s could not be read: %s",
                    button["year"],
                    error,
                )
                continue
            if not _calendar_is_usable(parsed, unmatched, expected_dates):
                _LOGGER.warning(
                    "Ignoring Affaldonline calendar %s for %s",
                    button["year"],
                    self._api_url,
                )
                continue
            entries.extend(parsed)
            parsed_a_year = True

        if not parsed_a_year:
            return None

        following = _following_year_button(buttons)
        if following is not None:
            try:
                parsed, unmatched = _parse_calendar_pdf(
                    self._download_calendar_pdf(following),
                    fallback_year=int(following["year"]),
                )
            except (requests.RequestException, ValueError):
                _LOGGER.debug(
                    "No Affaldonline calendar published for %s", following["year"]
                )
            else:
                if _calendar_is_usable(parsed, unmatched, None):
                    entries.extend(parsed)

        return _unique_collections(entries)

    def _parse_pdf(self, soup: BeautifulSoup) -> List[Collection]:
        raise ValueError(
            "No waste collection dates found in the Affaldonline calendar. "
            "Please check the provided values."
        )

    def _parse_default(self, soup: BeautifulSoup) -> List[Collection]:
        entries: List[Collection] = []

        next_pickup_info = soup.find_all(string=re.compile("Næste tømningsdag:"))
        if not next_pickup_info:
            raise ValueError(
                "No waste schemes found. Please check the provided values."
            )

        for info in next_pickup_info:
            text = info.strip()
            match = re.search(r"(\d{1,2})\. (\w+) (\d{4})", text)
            if match:
                try:
                    day = int(match.group(1))
                    month_name = match.group(2)
                    year = int(match.group(3))
                    month_index = DANISH_MONTHS.index(month_name) + 1
                    formatted_date = date(year, month_index, day)

                    # Extract waste types from the text
                    waste_type_search = re.search(r"\((.*?)\)", text)
                    if waste_type_search is None:
                        _LOGGER.warning("No waste type found in string: %s", text)
                        continue
                    waste_types_text = waste_type_search.group(1)

                    waste_types = [
                        waste_type.strip() for waste_type in waste_types_text.split(",")
                    ]

                    for waste_type in waste_types:
                        entries.append(Collection(date=formatted_date, t=waste_type))
                        _LOGGER.debug(
                            "Added collection: date=%s, type=%s",
                            formatted_date,
                            waste_type,
                        )
                except ValueError as e:
                    _LOGGER.error("Error parsing date: %s from string: %s", e, text)
            else:
                _LOGGER.warning("No valid date found in string: %s", text)

        return entries

    def _parse_silkeborg(self, soup: BeautifulSoup) -> List[Collection]:
        entries: List[Collection] = []

        table = soup.find("table")
        if not table:
            raise ValueError(
                "No waste collection table found. Please check the provided values."
            )

        current_year = datetime.now().year
        current_month = datetime.now().month

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                # Extract date and waste type
                date_str = cells[0].get_text(strip=True)
                waste_types = cells[1].get_text(strip=True)

                match = re.search(r"(\d{2})-(\d{2})", date_str)
                if match:
                    day = int(match.group(1))
                    month = int(match.group(2))

                    # Determine the year based on the current month
                    collection_year = current_year
                    if month < current_month:
                        collection_year += 1

                    collection_date = date(collection_year, month, day)

                    for waste_type in waste_types.split(","):
                        entries.append(
                            Collection(date=collection_date, t=waste_type.strip())
                        )
                        _LOGGER.debug(
                            "Added collection: date=%s, type=%s",
                            collection_date,
                            waste_type.strip(),
                        )

        return entries

    def _parse_favrskov(self, soup: BeautifulSoup) -> List[Collection]:
        entries: List[Collection] = []

        strong_tags = soup.find_all("strong")
        if not strong_tags:
            raise ValueError(
                "No waste schemes found. Please check the provided values."
            )

        for strong_tag in strong_tags:
            waste_type = strong_tag.get_text(strip=True)
            next_sibling = strong_tag.find_next_sibling(text=True)
            if next_sibling and "Næste tømningsdag" in next_sibling:
                match = re.search(r"(\d{1,2})\. (\w+) (\d{4})", next_sibling)
                if match:
                    try:
                        day = int(match.group(1))
                        month_name = match.group(2)
                        year = int(match.group(3))
                        month_index = DANISH_MONTHS.index(month_name) + 1
                        formatted_date = date(year, month_index, day)

                        entries.append(Collection(date=formatted_date, t=waste_type))
                        _LOGGER.debug(
                            "Added collection: date=%s, type=%s",
                            formatted_date,
                            waste_type,
                        )
                    except ValueError as e:
                        _LOGGER.error(
                            "Error parsing date: %s from string: %s", e, next_sibling
                        )
                else:
                    _LOGGER.warning("No valid date found in string: %s", next_sibling)

        return entries
