import calendar as _stdlib_calendar  # noqa: F401
import json
import os
import sys
from collections import defaultdict
from datetime import date

import pytest
from pdfminer.high_level import extract_text

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "custom_components",
            "waste_collection_schedule",
        )
    ),
)

from waste_collection_schedule.source import affaldonline_dk

FIXTURE_PDF = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "affaldonline_holbaek_2026.pdf",
)


class MockResponse:
    def __init__(self, text="", json_data=None, status_code=200, content=None):
        self.text = text
        self._json_data = json_data
        self.status_code = status_code
        self.content = text.encode() if content is None else content

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise affaldonline_dk.requests.HTTPError()


def test_resolves_address_fields_to_affaldonline_values(monkeypatch):
    if hasattr(affaldonline_dk, "_resolve_values"):
        affaldonline_dk._resolve_values.cache_clear()

    street_response = MockResponse(
        json_data=[
            {
                "value": "Lookupvej (1000 Otherby)",
                "vejnavn": "Lookupvej",
                "postnr": "1000",
                "Bynavn": "Otherby",
            },
            {
                "value": "Lookupvej (1234 Lookupby)",
                "vejnavn": "Lookupvej",
                "postnr": "1234",
                "Bynavn": "Lookupby",
            },
        ]
    )
    house_response = MockResponse(
        text="""
        <select id="SelHusNr" name="values">
            <option value="Lookupvej|11||||1234|Lookupby|100|200|0">11</option>
            <option value="Lookupvej|12||||1234|Lookupby|101|201|0">12</option>
        </select>
        """
    )
    schedule_response = MockResponse(
        text="Næste tømningsdag: mandag den 6. juli 2026 (Rest/Mad, Pap/Papir)"
    )
    get_responses = [street_response, house_response]
    get_calls = []
    posted_data = []

    def mock_get(url, params):
        get_calls.append((url, params))
        assert url in {
            "https://www.affaldonline.dk/kalender/holbaek/acCal.php",
            "https://www.affaldonline.dk/kalender/holbaek/husnrCal.php",
        }
        return get_responses.pop(0)

    def mock_post(url, data):
        posted_data.append(data)
        return schedule_response

    monkeypatch.setattr(affaldonline_dk.requests, "get", mock_get)
    monkeypatch.setattr(affaldonline_dk.requests, "post", mock_post)

    source_kwargs = {
        "municipality": "holbaek",
        "street": "Lookupvej",
        "house_number": "12",
        "postal_code": "1234",
        "city": "Lookupby",
    }
    source = affaldonline_dk.Source(**source_kwargs)
    cached_source = affaldonline_dk.Source(**source_kwargs)

    entries = source.fetch()
    cached_source.fetch()

    assert len(get_calls) == 2
    assert posted_data == [
        {"values": "Lookupvej|12||||1234|Lookupby|101|201|0"},
        {"values": "Lookupvej|12||||1234|Lookupby|101|201|0"},
    ]
    assert [(entry.date, entry.type) for entry in entries] == [
        (date(2026, 7, 6), "Rest/Mad"),
        (date(2026, 7, 6), "Pap/Papir"),
    ]


def test_resolves_compact_compound_house_number(monkeypatch):
    if hasattr(affaldonline_dk, "_resolve_values"):
        affaldonline_dk._resolve_values.cache_clear()

    def mock_get(url, params):
        if url.endswith("/acCal.php"):
            return MockResponse(
                json_data=[
                    {
                        "value": "Compoundvej (2345 Actualby)",
                        "vejnavn": "Compoundvej",
                        "postnr": "2345",
                        "Bynavn": "Actualby",
                    }
                ]
            )

        assert params == {
            "vejnavn": "Compoundvej",
            "postnr": "2345",
            "postdist": "Actualby",
        }
        return MockResponse(
            text="""
            <select id="SelHusNr" name="values">
                <option value="Compoundvej|11||ST|TV|2345|Actualby|300|400|695">11 ST TV</option>
                <option value="Compoundvej|37|A||i|2345|Actualby|301|401|0">37 A i</option>
            </select>
            """
        )

    monkeypatch.setattr(affaldonline_dk.requests, "get", mock_get)

    source = affaldonline_dk.Source(
        municipality="holbaek",
        street="Compoundvej",
        house_number="37Ai",
        postal_code="2345",
        city="Aliasby",
    )

    assert source._values == "Compoundvej|37|A||i|2345|Actualby|301|401|0"


def test_single_day_multi_fraction_line():
    soup = affaldonline_dk.BeautifulSoup(
        "Næste tømningsdag: mandag den 6. juli 2026 (Rest/Mad, Pap/Papir)",
        "html.parser",
    )
    source = affaldonline_dk.Source(
        municipality="aeroe",
        values="Nørregade|1||||5970|Ærøskøbing|1228262|448776|0",
    )

    entries = source._parse_default(soup)

    assert [(entry.date, entry.type) for entry in entries] == [
        (date(2026, 7, 6), "Rest/Mad"),
        (date(2026, 7, 6), "Pap/Papir"),
    ]


def test_holbaek_calendar_returns_dates_beyond_next_emptying(monkeypatch):
    pdf_bytes = open(FIXTURE_PDF, "rb").read()
    show_info = """
        Næste tømningsdag:&nbsp;Torsdag den 8. oktober 2026 (Rest, Mad, Pap/Papir)
        <button class="btn" type="button"
            onClick="window.open('showToemCal.php?year=2026&forbid=1676484&altid=0&type=1')">
            Kalender 2026
        </button>
    """
    downloaded = []

    def mock_post(url, data=None, **kwargs):
        assert url == "https://www.affaldonline.dk/kalender/holbaek/showInfo.php"
        return MockResponse(text=show_info)

    def mock_get(url, params=None, timeout=None, **kwargs):
        downloaded.append((url, params))
        if params and str(params.get("year")) == "2026":
            return MockResponse(content=pdf_bytes)
        return MockResponse(text="not a pdf", content=b"not a pdf")

    monkeypatch.setattr(affaldonline_dk.requests, "post", mock_post)
    monkeypatch.setattr(affaldonline_dk.requests, "get", mock_get)

    source = affaldonline_dk.Source(
        municipality="holbaek",
        values="Tåstrup Møllevej|5||||4300|Holbæk|76490500|1676484|0",
    )
    entries = source.fetch()

    by_date = defaultdict(set)
    for entry in entries:
        by_date[entry.date].add(entry.type)

    assert [params["year"] for _url, params in downloaded] == ["2026", "2027"]
    assert downloaded[0][1]["forbid"] == "1676484"
    assert by_date[date(2026, 10, 8)] == {"Rest", "Mad", "Pap/Papir"}
    assert by_date[date(2026, 1, 3)] == {"Rest", "Mad", "Pap"}
    assert [
        day for day in sorted(by_date) if date(2026, 3, 23) <= day <= date(2026, 3, 29)
    ] == [
        date(2026, 3, 25),
        date(2026, 3, 26),
    ]
    assert any(day > date(2026, 10, 8) for day in by_date)
    assert date(2026, 12, 31) in by_date
    assert len(by_date) > 20


def test_unreadable_calendar_keeps_next_emptying(monkeypatch):
    def mock_get(url, params=None, timeout=None, **kwargs):
        assert "showToemCal.php" in url
        return MockResponse(text="not a pdf", content=b"not a pdf")

    def mock_post(url, data=None, **kwargs):
        assert "/kalender/aeroe/showInfo.php" in url
        return MockResponse(
            text=(
                "Næste tømningsdag: mandag den 12. oktober 2026 (Mad/Rest)"
                "<button onClick=\"window.open('showToemCal.php?year=2026"
                "&forbid=1&altid=0&type=1')\">Kalender 2026</button>"
            )
        )

    monkeypatch.setattr(affaldonline_dk.requests, "get", mock_get)
    monkeypatch.setattr(affaldonline_dk.requests, "post", mock_post)

    source = affaldonline_dk.Source(
        municipality="aeroe",
        values="Nørregade|1||||5970|Ærøskøbing|1228262|448776|0",
    )

    entries = source.fetch()

    assert [(entry.date, entry.type) for entry in entries] == [
        (date(2026, 10, 12), "Mad/Rest"),
    ]


def test_compact_weekday_is_a_date_not_a_waste_type():
    assert affaldonline_dk._DATE_LINE_RE.match("MAN12")
    assert affaldonline_dk._DATE_LINE_RE.match("LØR 3")
    assert not affaldonline_dk._is_legend_label("MAN23")
    assert affaldonline_dk._is_legend_label("Rest/Mad")


def test_calendar_button_keeps_extra_query_params():
    soup = affaldonline_dk.BeautifulSoup(
        "<button onClick=\"window.open('showToemCal.php?year=2026"
        "&forbid=8838&altid=0&pnr=0&type=1')\">Kalender 2026</button>",
        "html.parser",
    )

    buttons = affaldonline_dk._calendar_buttons(soup)
    following = affaldonline_dk._following_year_button(buttons)

    assert buttons == [
        {
            "year": "2026",
            "forbid": "8838",
            "altid": "0",
            "pnr": "0",
            "type": "1",
        }
    ]
    assert following["year"] == "2027"
    assert following["pnr"] == "0"


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
with open(
    os.path.join(FIXTURE_DIR, "affaldonline_calendar_cases.json"),
    encoding="utf-8",
) as calendar_cases_file:
    CALENDAR_CASES = json.load(calendar_cases_file)

# Concrete dates and waste types recorded from each public sample calendar.
# Holbæk stays on tests/fixtures/affaldonline_holbaek_2026.pdf and
# test_holbaek_calendar_returns_dates_beyond_next_emptying.
CALENDAR_ANCHORS = {
    "aeroe": {
        "dates": 33,
        "next_emptying": (date(2026, 10, 12),),
        "later": date(2026, 12, 21),
        "types": {
            "Glas",
            "Madaffald",
            "Metal",
            "Miljøkasse",
            "Papir/Pap",
            "Plast/Drikkekarton",
            "Restaffald",
            "Tekstiler",
        },
        "days": {
            date(2026, 1, 5): {
                "Glas",
                "Madaffald",
                "Miljøkasse",
                "Papir/Pap",
                "Restaffald",
                "Tekstiler",
            },
            date(2026, 7, 6): {
                "Madaffald",
                "Metal",
                "Plast/Drikkekarton",
                "Restaffald",
            },
            date(2026, 10, 12): {
                "Glas",
                "Madaffald",
                "Miljøkasse",
                "Papir/Pap",
                "Restaffald",
                "Tekstiler",
            },
            date(2026, 12, 21): {
                "Madaffald",
                "Metal",
                "Plast/Drikkekarton",
                "Restaffald",
            },
        },
    },
    "assens": {
        "dates": 26,
        "next_emptying": (date(2026, 10, 13),),
        "later": date(2026, 12, 21),
        "types": {"Restaffald/Madaffald"},
        "days": {
            date(2026, 1, 6): {"Restaffald/Madaffald"},
            date(2026, 7, 7): {"Restaffald/Madaffald"},
            date(2026, 10, 13): {"Restaffald/Madaffald"},
            date(2026, 12, 21): {"Restaffald/Madaffald"},
        },
    },
    "favrskov": {
        "dates": 87,
        "next_emptying": (date(2026, 10, 5), date(2026, 10, 8)),
        "later": date(2026, 12, 28),
        "types": {
            "Glas/metal",
            "Madaffald",
            "Papir/pap og tekstiler",
            "Plast/mad- og drikkekartoner",
            "Restaffald",
        },
        "days": {
            date(2026, 1, 3): {"Papir/pap og tekstiler"},
            date(2026, 6, 29): {"Madaffald", "Restaffald"},
            date(2026, 10, 5): {"Madaffald", "Restaffald"},
            date(2026, 10, 8): {
                "Glas/metal",
                "Papir/pap og tekstiler",
                "Plast/mad- og drikkekartoner",
            },
            date(2026, 12, 28): {"Madaffald", "Restaffald"},
        },
    },
    "fanoe": {
        "dates": 30,
        "next_emptying": (),
        "later": None,
        "types": {"Bioaffald", "Restaffald"},
        "days": {
            date(2026, 1, 12): {"Bioaffald", "Restaffald"},
            date(2026, 7, 20): {"Bioaffald"},
            date(2026, 12, 28): {"Bioaffald", "Restaffald"},
        },
    },
    "ffv": {
        "dates": 28,
        "next_emptying": (date(2026, 10, 16),),
        "later": date(2026, 12, 26),
        "types": {
            "Papir/småt pap og glas/metal",
            "Plast/fødevarekarton",
            "Rest-/madaffald",
            "Røde kasser og tekstiler",
        },
        "days": {
            date(2026, 1, 9): {"Papir/småt pap og glas/metal", "Rest-/madaffald"},
            date(2026, 7, 10): {"Plast/fødevarekarton", "Rest-/madaffald"},
            date(2026, 10, 16): {"Papir/småt pap og glas/metal", "Rest-/madaffald"},
            date(2026, 12, 26): {"Plast/fødevarekarton", "Rest-/madaffald"},
        },
    },
    "fredericia": {
        "dates": 53,
        "next_emptying": (),
        "later": None,
        "types": {"Madaffald", "Restaffald"},
        "days": {
            date(2026, 1, 3): {"Madaffald", "Restaffald"},
            date(2026, 7, 2): {"Madaffald", "Restaffald"},
            date(2026, 12, 31): {"Madaffald", "Restaffald"},
        },
    },
    "langeland": {
        "dates": 26,
        "next_emptying": (date(2026, 10, 5),),
        "later": date(2026, 12, 28),
        "types": {
            "Bioaffald",
            "Miljøkasse",
            "Pap/papir og glas/metal",
            "Plast/Drikkekarton",
            "Restaffald",
            "Tekstilaffald",
        },
        "days": {
            date(2026, 1, 12): {
                "Bioaffald",
                "Plast/Drikkekarton",
                "Restaffald",
                "Tekstilaffald",
            },
            date(2026, 7, 13): {
                "Bioaffald",
                "Miljøkasse",
                "Pap/papir og glas/metal",
                "Restaffald",
            },
            date(2026, 10, 5): {
                "Bioaffald",
                "Miljøkasse",
                "Pap/papir og glas/metal",
                "Restaffald",
            },
            date(2026, 12, 28): {
                "Bioaffald",
                "Miljøkasse",
                "Pap/papir og glas/metal",
                "Restaffald",
            },
        },
    },
    "middelfart": {
        "dates": 45,
        "next_emptying": (date(2026, 10, 5),),
        "later": date(2026, 12, 30),
        "types": {
            "Glas/Metal",
            "Papir/Pap og Plast/Mad- og drikkekartoner",
            "Restaffald/Madaffald",
        },
        "days": {
            date(2026, 1, 14): {"Restaffald/Madaffald"},
            date(2026, 7, 6): {"Papir/Pap og Plast/Mad- og drikkekartoner"},
            date(2026, 10, 5): {"Glas/Metal"},
            date(2026, 12, 30): {"Restaffald/Madaffald"},
        },
    },
    "nyborg": {
        "dates": 67,
        "next_emptying": (),
        "later": None,
        "types": {
            "Haveaffald",
            "Papir/Pap/Plast/Mad-drikkekarton",
            "Restaffald",
        },
        "days": {
            date(2026, 1, 6): {
                "Haveaffald",
                "Papir/Pap/Plast/Mad-drikkekarton",
                "Restaffald",
            },
            date(2026, 7, 7): {"Haveaffald", "Restaffald"},
            date(2027, 7, 13): {"Haveaffald", "Restaffald"},
            date(2027, 12, 27): {"Haveaffald", "Restaffald"},
        },
    },
    "silkeborg": {
        "dates": 44,
        "next_emptying": (),
        "later": None,
        "types": {"Mad-/Rest", "Plast/MDK/Glas/Metal/Papir/Pap"},
        "days": {
            date(2026, 1, 2): {"Plast/MDK/Glas/Metal/Papir/Pap"},
            date(2026, 7, 8): {"Mad-/Rest"},
            date(2026, 12, 27): {"Plast/MDK/Glas/Metal/Papir/Pap"},
        },
    },
    "soroe": {
        "dates": 55,
        "next_emptying": (),
        "later": None,
        "types": {
            "Glas/metal",
            "Haveaffald",
            "Plast/mad- og drikkekarton og pap/papir",
            "Rest-/madaffald",
            "Storskrald",
        },
        "days": {
            date(2026, 1, 8): {"Plast/mad- og drikkekarton og pap/papir"},
            date(2026, 7, 3): {"Haveaffald"},
            date(2026, 12, 31): {"Plast/mad- og drikkekarton og pap/papir"},
        },
    },
    "viborg": {
        "dates": 69,
        "next_emptying": (date(2026, 10, 6),),
        "later": date(2026, 12, 28),
        "types": {"Glas/Metal", "Mad", "Papir/Pap", "Plast/MDK", "Rest"},
        "days": {
            date(2026, 1, 6): {"Mad", "Rest"},
            date(2026, 7, 7): {"Glas/Metal", "Mad", "Plast/MDK", "Rest"},
            date(2026, 10, 6): {"Mad", "Rest"},
            date(2026, 12, 28): {"Mad", "Rest"},
        },
    },
}

# These sample addresses publish a calendar link, but the PDF has no collection
# rows and the next-emptying line has no date. The source must keep that
# fallback instead of inventing dates from the month headings.
NO_COLLECTION_DATES = {
    "rebild": "Tømningskalender",
    "vejle": "Der kunne ikke findes nogle tømmedatoer",
}


def _pdf_path(municipality, year):
    return os.path.join(FIXTURE_DIR, f"affaldonline_{municipality}_{year}.pdf")


def _fetch_recorded_case(monkeypatch, municipality):
    case = CALENDAR_CASES[municipality]
    pdfs = {}
    for year in case["pdf_years"]:
        with open(_pdf_path(municipality, year), "rb") as handle:
            pdfs[year] = handle.read()
    downloaded = []

    def mock_post(url, data=None, **kwargs):
        assert (
            url == f"https://www.affaldonline.dk/kalender/{municipality}/showInfo.php"
        )
        assert data == {
            "values": affaldonline_dk.AFFALDONLINE_MUNICIPALITIES[municipality][
                "values"
            ]
        }
        return MockResponse(text=case["show_info"])

    def mock_get(url, params=None, timeout=None, **kwargs):
        assert url == (
            f"https://www.affaldonline.dk/kalender/{municipality}/showToemCal.php"
        )
        downloaded.append(params)
        year = str(params.get("year"))
        if year in pdfs:
            assert pdfs[year].startswith(b"%PDF")
            return MockResponse(content=pdfs[year])
        return MockResponse(text="not a pdf", content=b"not a pdf")

    monkeypatch.setattr(affaldonline_dk.requests, "post", mock_post)
    monkeypatch.setattr(affaldonline_dk.requests, "get", mock_get)

    source = affaldonline_dk.Source(
        municipality=municipality,
        values=affaldonline_dk.AFFALDONLINE_MUNICIPALITIES[municipality]["values"],
    )
    return source.fetch(), downloaded, pdfs


def test_every_municipality_has_an_offline_calendar_case():
    recorded = set(CALENDAR_CASES)
    assert "holbaek" not in recorded
    assert recorded | {"holbaek"} == set(affaldonline_dk.AFFALDONLINE_MUNICIPALITIES)
    assert set(CALENDAR_ANCHORS) | set(NO_COLLECTION_DATES) == recorded
    assert os.path.isfile(os.path.join(FIXTURE_DIR, "affaldonline_holbaek_2026.pdf"))
    blob = json.dumps(CALENDAR_CASES)
    assert "Nyvej" not in blob
    assert "Tølløse" not in blob


@pytest.mark.parametrize("municipality", sorted(CALENDAR_ANCHORS))
def test_recorded_calendar_pdf_matches_concrete_dates(monkeypatch, municipality):
    spec = CALENDAR_ANCHORS[municipality]
    case = CALENDAR_CASES[municipality]
    entries, downloaded, pdfs = _fetch_recorded_case(monkeypatch, municipality)

    by_date = defaultdict(set)
    for entry in entries:
        by_date[entry.date].add(entry.type)
        assert affaldonline_dk._DATE_LINE_RE.match(entry.type) is None
        assert affaldonline_dk._is_legend_label(entry.type)

    assert len(by_date) > 1
    assert len(by_date) == spec["dates"]
    assert {entry_type for types in by_date.values() for entry_type in types} == spec[
        "types"
    ]
    for day, waste_types in spec["days"].items():
        assert by_date[day] == waste_types

    soup = affaldonline_dk.BeautifulSoup(case["show_info"], "html.parser")
    next_emptying = affaldonline_dk._next_emptying_dates(soup)
    assert next_emptying == set(spec["next_emptying"])
    assert next_emptying <= set(by_date)
    if spec["later"] is not None:
        assert spec["later"] in by_date
        assert spec["later"] > max(next_emptying)

    assert [(entry.date.isoformat(), entry.type) for entry in entries] == case[
        "collections"
    ]
    assert [params["year"] for params in downloaded] == ["2026", "2027"]
    buttons = affaldonline_dk._calendar_buttons(soup)
    following = affaldonline_dk._following_year_button(buttons)
    assert downloaded == [*buttons, following]

    for year, pdf_bytes in pdfs.items():
        parsed, unmatched = affaldonline_dk._parse_calendar_pdf(
            pdf_bytes, fallback_year=int(year)
        )
        assert unmatched == 0
        assert len({entry.date for entry in parsed}) > 1
        assert all(affaldonline_dk._is_legend_label(entry.type) for entry in parsed)


@pytest.mark.parametrize("municipality", sorted(NO_COLLECTION_DATES))
def test_sample_without_collection_dates_uses_next_emptying_line(
    monkeypatch, municipality
):
    case = CALENDAR_CASES[municipality]
    entries, downloaded, pdfs = _fetch_recorded_case(monkeypatch, municipality)

    assert entries == []
    assert case["collections"] == []
    assert [params["year"] for params in downloaded] == ["2026"]

    soup = affaldonline_dk.BeautifulSoup(case["show_info"], "html.parser")
    assert affaldonline_dk._calendar_buttons(soup)
    assert affaldonline_dk._next_emptying_dates(soup) == set()
    assert downloaded == affaldonline_dk._calendar_buttons(soup)

    source = affaldonline_dk.Source(
        municipality=municipality,
        values=affaldonline_dk.AFFALDONLINE_MUNICIPALITIES[municipality]["values"],
    )
    assert source._parse_default(soup) == []

    pdf_bytes = pdfs["2026"]
    parsed, unmatched = affaldonline_dk._parse_calendar_pdf(
        pdf_bytes, fallback_year=2026
    )
    assert parsed == []
    assert unmatched == 0
    assert NO_COLLECTION_DATES[municipality] in extract_text(pdf_bytes)
