import calendar as _stdlib_calendar  # noqa: F401
import json
import os
import sys
from collections import defaultdict
from datetime import date
from io import BytesIO

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

from waste_collection_schedule.service import Affaldonline as affaldonline
from waste_collection_schedule.source import affaldonline_dk

FIXTURE_PDF = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "affaldonline_holbaek_2026.pdf",
)


class FakeResponse:
    def __init__(self, url, text="", json_data=None, status_code=200, content=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self._json_data = json_data
        self.content = text.encode() if content is None else content

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(self.status_code)


class FakeSession:
    def __init__(self, get_responses, post_response):
        self._get_responses = list(get_responses)
        self.get_calls = []
        self.posted = []
        self._post_response = post_response

    def get(self, url, params=None, **kwargs):
        self.get_calls.append((url, params))
        if not self._get_responses:
            return FakeResponse(url, text="not a pdf", content=b"not a pdf")
        response = self._get_responses.pop(0)
        response.url = url
        return response

    def post(self, url, data=None, **kwargs):
        self.posted.append(data)
        self._post_response.url = url
        return self._post_response


def _bind(source, session):
    source._session = session
    return source


def test_resolves_address_fields_to_affaldonline_values():
    affaldonline.clear_value_cache()
    street_response = FakeResponse(
        "",
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
        ],
    )
    house_response = FakeResponse(
        "",
        text="""
        <select id="SelHusNr" name="values">
            <option value="Lookupvej|11||||1234|Lookupby|100|200|0">11</option>
            <option value="Lookupvej|12||||1234|Lookupby|101|201|0">12</option>
        </select>
        """,
    )
    schedule = FakeResponse(
        "",
        text="Næste tømningsdag: mandag den 6. juli 2026 (Rest/Mad, Pap/Papir)",
    )
    session = FakeSession([street_response, house_response], schedule)
    kwargs = {
        "municipality": "holbaek",
        "street": "Lookupvej",
        "house_number": "12",
        "postal_code": "1234",
        "city": "Lookupby",
    }
    first = _bind(affaldonline_dk.Source(**kwargs), session)
    second = _bind(affaldonline_dk.Source(**kwargs), session)

    entries = first.fetch()
    second.fetch()

    assert [url for url, _params in session.get_calls] == [
        "https://www.affaldonline.dk/kalender/holbaek/acCal.php",
        "https://www.affaldonline.dk/kalender/holbaek/husnrCal.php",
    ]
    assert session.posted == [
        {"values": "Lookupvej|12||||1234|Lookupby|101|201|0"},
        {"values": "Lookupvej|12||||1234|Lookupby|101|201|0"},
    ]
    assert [(entry.date, entry.description) for entry in entries] == [
        (date(2026, 7, 6), "Rest/Mad"),
        (date(2026, 7, 6), "Pap/Papir"),
    ]
    assert len({entry.date for entry in entries}) == 1


def test_resolves_compact_compound_house_number():
    affaldonline.clear_value_cache()

    def get(url, params=None, **kwargs):
        if url.endswith("/acCal.php"):
            return FakeResponse(
                url,
                json_data=[
                    {
                        "value": "Compoundvej (2345 Actualby)",
                        "vejnavn": "Compoundvej",
                        "postnr": "2345",
                        "Bynavn": "Actualby",
                    }
                ],
            )
        assert params == {
            "vejnavn": "Compoundvej",
            "postnr": "2345",
            "postdist": "Actualby",
        }
        return FakeResponse(
            url,
            text="""
            <select id="SelHusNr" name="values">
                <option value="Compoundvej|11||ST|TV|2345|Actualby|300|400|695">11 ST TV</option>
                <option value="Compoundvej|37|A||i|2345|Actualby|301|401|0">37 A i</option>
            </select>
            """,
        )

    source = affaldonline_dk.Source(
        municipality="holbaek",
        street="Compoundvej",
        house_number="37Ai",
        postal_code="2345",
        city="Aliasby",
    )
    source._session = type("Session", (), {"get": staticmethod(get), "post": None})()
    assert (
        affaldonline.resolve_values(source)
        == "Compoundvej|37|A||i|2345|Actualby|301|401|0"
    )


def test_holbaek_calendar_returns_dates_beyond_next_emptying():
    affaldonline.clear_value_cache()
    pdf_bytes = open(FIXTURE_PDF, "rb").read()
    show_info = FakeResponse(
        "",
        text="""
        Næste tømningsdag:&nbsp;Torsdag den 8. oktober 2026 (Rest, Mad, Pap/Papir)
        <button class="btn" type="button"
            onClick="window.open('showToemCal.php?year=2026&forbid=1676484&altid=0&type=1')">
            Kalender 2026
        </button>
        """,
    )
    session = FakeSession(
        [FakeResponse("", content=pdf_bytes), FakeResponse("", content=b"not a pdf")],
        show_info,
    )
    source = _bind(
        affaldonline_dk.Source(
            municipality="holbaek",
            values="Tåstrup Møllevej|5||||4300|Holbæk|76490500|1676484|0",
        ),
        session,
    )

    entries = source.fetch()
    by_date = defaultdict(set)
    described = defaultdict(set)
    for entry in entries:
        by_date[entry.date].add(entry.waste_type.id)
        described[entry.date].add(entry.description)

    assert [params["year"] for _url, params in session.get_calls] == ["2026", "2027"]
    assert session.get_calls[0][1]["forbid"] == "1676484"
    assert len(by_date) > 1
    assert len(by_date) > 20
    assert described[date(2026, 10, 8)] == {"Rest", "Mad", "Pap/Papir"}
    assert by_date[date(2026, 10, 8)] == {"general_waste", "food_waste", "paper"}
    assert described[date(2026, 1, 3)] == {"Rest", "Mad", "Pap"}
    assert [
        day for day in sorted(by_date) if date(2026, 3, 23) <= day <= date(2026, 3, 29)
    ] == [date(2026, 3, 25), date(2026, 3, 26)]
    assert any(day > date(2026, 10, 8) for day in by_date)
    assert date(2026, 12, 31) in by_date


def test_unreadable_calendar_keeps_next_emptying():
    affaldonline.clear_value_cache()
    session = FakeSession(
        [],
        FakeResponse(
            "",
            text=(
                "Næste tømningsdag: mandag den 12. oktober 2026 (Mad/Rest)"
                "<button onClick=\"window.open('showToemCal.php?year=2026"
                "&forbid=1&altid=0&type=1')\">Kalender 2026</button>"
            ),
        ),
    )
    source = _bind(
        affaldonline_dk.Source(
            municipality="aeroe",
            values="Nørregade|1||||5970|Ærøskøbing|1228262|448776|0",
        ),
        session,
    )

    entries = source.fetch()

    assert [(entry.date, entry.description) for entry in entries] == [
        (date(2026, 10, 12), "Mad/Rest"),
    ]
    assert all("showToemCal.php" in url for url, _params in session.get_calls)


def test_compact_weekday_is_a_date_not_a_waste_type():
    assert affaldonline._DATE_LINE_RE.match("MAN12")
    assert affaldonline._DATE_LINE_RE.match("LØR 3")
    assert not affaldonline._is_legend_label("MAN23")
    assert affaldonline._is_legend_label("Rest/Mad")


def test_calendar_button_keeps_extra_query_params():
    soup = affaldonline.BeautifulSoup(
        "<button onClick=\"window.open('showToemCal.php?year=2026"
        "&forbid=8838&altid=0&pnr=0&type=1')\">Kalender 2026</button>",
        "html.parser",
    )

    buttons = affaldonline._calendar_buttons(soup)
    following = affaldonline._following_year_button(buttons)

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

# Public sample ``values`` strings from AFFALDONLINE_MUNICIPALITIES. The 3.0
# municipality table does not repeat them; these are the same addresses.
SAMPLE_VALUES = {
    "aeroe": "Nørregade|1||||5970|Ærøskøbing|1228262|448776|0",
    "assens": "Nørregade|1||||5610|Assens|10894|430000|0",
    "favrskov": "Nørregade|1||||8382|Hinnerup|6443|108156|0",
    "fanoe": "Nørre Klit|5||||6720|Fanø|2582|1747246|0",
    "ffv": "Marsk Billesvej|18||||5672|Broby|36193544|576846|0",
    "fredericia": "Nørre Allé|5||||7000|Fredericia|11079971|1907927|0",
    "langeland": "Nørregade|1||||5900|Rudkøbing|3535|383566|0",
    "middelfart": "Nørregade|2||||5592|Ejby|11288085|6496420|0",
    "nyborg": "Nørregade|5||||5800|Nyborg|8896288|552542|0",
    "rebild": "Nørregade|1||||9500|Hobro|4676913|1012588|0",
    "silkeborg": "Nørregade|5||||8620|Kjellerup|45814316|1291964|0",
    "soroe": "Nørrevej|4| |||4180|Sorø|8569|8838|0|0",
    "vejle": "Nørregade|69||||7100|Vejle|16285351|16285351|0",
    "viborg": "Hjultorvet|1||||8800|Viborg|8228245|8739|0",
}

# Legend text from the PDF, and the waste type the 3.0 pipeline assigns.
# ``preserved:`` means the shared vocabulary does not classify that label yet;
# the original wording is still the type the test locks.
LABEL_IDS = {
    "Glas": "glass",
    "Madaffald": "food_waste",
    "Metal": "recyclables",
    "Miljøkasse": "hazardous",
    "Papir/Pap": "paper",
    "Plast/Drikkekarton": "recyclables",
    "Restaffald": "general_waste",
    "Tekstiler": "textiles",
    "Restaffald/Madaffald": "preserved:Restaffald/Madaffald",
    "Papir/pap og tekstiler": "preserved:Papir/pap og tekstiler",
    "Glas/metal": "preserved:Glas/metal",
    "Plast/mad- og drikkekartoner": "preserved:Plast/mad- og drikkekartoner",
    "Bioaffald": "food_waste",
    "Papir/småt pap og glas/metal": "recyclables",
    "Plast/fødevarekarton": "recyclables",
    "Rest-/madaffald": "general_waste",
    "Røde kasser og tekstiler": "preserved:Røde kasser og tekstiler",
    "Pap/papir og glas/metal": "recyclables",
    "Tekstilaffald": "textiles",
    "Glas/Metal": "preserved:Glas/Metal",
    "Papir/Pap og Plast/Mad- og drikkekartoner": "recyclables",
    "Haveaffald": "garden_waste",
    "Papir/Pap/Plast/Mad-drikkekarton": "preserved:Papir/Pap/Plast/Mad-drikkekarton",
    "Mad-/Rest": "preserved:Mad-/Rest",
    "Plast/MDK/Glas/Metal/Papir/Pap": "preserved:Plast/MDK/Glas/Metal/Papir/Pap",
    "Plast/mad- og drikkekarton og pap/papir": (
        "preserved:Plast/mad- og drikkekarton og pap/papir"
    ),
    "Storskrald": "bulky_waste",
    "Mad": "food_waste",
    "Plast/MDK": "recyclables",
    "Rest": "general_waste",
}

CALENDAR_ANCHORS = {
    "aeroe": {
        "dates": 33,
        "next_emptying": (date(2026, 10, 12),),
        "later": date(2026, 12, 21),
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
        "days": {
            date(2026, 1, 6): {"Mad", "Rest"},
            date(2026, 7, 7): {"Glas/Metal", "Mad", "Plast/MDK", "Rest"},
            date(2026, 10, 6): {"Mad", "Rest"},
            date(2026, 12, 28): {"Mad", "Rest"},
        },
    },
}

# Calendar link, but the PDF has no collection rows and the next-emptying line
# has no date. The 3.0 fallback raises instead of inventing a schedule.
NO_COLLECTION_DATES = {
    "rebild": "Tømningskalender",
    "vejle": "Der kunne ikke findes nogle tømmedatoer",
}


def _pdf_path(municipality, year):
    return os.path.join(FIXTURE_DIR, f"affaldonline_{municipality}_{year}.pdf")


def _provider_label(entry):
    if entry.description_is_raw_label_fallback and entry.description:
        return entry.description
    type_id = entry.waste_type.id
    prefix = "preserved:"
    if type_id.startswith(prefix):
        return type_id.removeprefix(prefix)
    raise AssertionError(f"no provider label on {entry.waste_type.id}")


def _fetch_recorded_case(municipality):
    case = CALENDAR_CASES[municipality]
    pdfs = []
    for year in case["pdf_years"]:
        with open(_pdf_path(municipality, year), "rb") as handle:
            pdfs.append(FakeResponse("", content=handle.read()))
    session = FakeSession(pdfs, FakeResponse("", text=case["show_info"]))
    affaldonline.clear_value_cache()
    source = _bind(
        affaldonline_dk.Source(
            municipality=municipality,
            values=SAMPLE_VALUES[municipality],
        ),
        session,
    )
    return source, session


def test_every_municipality_has_an_offline_calendar_case():
    recorded = set(CALENDAR_CASES)
    assert "holbaek" not in recorded
    assert recorded | {"holbaek"} == set(affaldonline.MUNICIPALITIES)
    assert set(CALENDAR_ANCHORS) | set(NO_COLLECTION_DATES) == recorded
    assert set(SAMPLE_VALUES) == recorded
    assert os.path.isfile(FIXTURE_PDF)
    blob = json.dumps(CALENDAR_CASES) + json.dumps(SAMPLE_VALUES)
    assert "Nyvej" not in blob
    assert "Tølløse" not in blob
    assert "4340" not in blob


@pytest.mark.parametrize("municipality", sorted(CALENDAR_ANCHORS))
def test_recorded_calendar_pdf_matches_concrete_dates(municipality):
    spec = CALENDAR_ANCHORS[municipality]
    case = CALENDAR_CASES[municipality]
    source, session = _fetch_recorded_case(municipality)
    entries = source.fetch()

    by_label = defaultdict(set)
    by_id = defaultdict(set)
    for entry in entries:
        label = _provider_label(entry)
        assert affaldonline._DATE_LINE_RE.match(label) is None
        assert affaldonline._is_legend_label(label)
        assert entry.waste_type.id == LABEL_IDS[label]
        by_label[entry.date].add(label)
        by_id[entry.date].add(entry.waste_type.id)

    assert len(by_label) > 1
    assert len(by_label) == spec["dates"]
    for day, labels in spec["days"].items():
        assert by_label[day] == labels
        assert by_id[day] == {LABEL_IDS[label] for label in labels}

    soup = affaldonline.BeautifulSoup(case["show_info"], "html.parser")
    next_emptying = affaldonline._next_emptying_dates(soup)
    assert next_emptying == set(spec["next_emptying"])
    assert next_emptying <= set(by_label)
    if spec["later"] is not None:
        assert spec["later"] in by_label
        assert spec["later"] > max(next_emptying)

    assert [(entry.date.isoformat(), _provider_label(entry)) for entry in entries] == [
        tuple(item) for item in case["collections"]
    ]
    assert session.posted == [{"values": SAMPLE_VALUES[municipality]}]
    buttons = affaldonline._calendar_buttons(soup)
    following = affaldonline._following_year_button(buttons)
    assert [params for _url, params in session.get_calls] == [*buttons, following]
    assert [params["year"] for _url, params in session.get_calls] == ["2026", "2027"]

    for year in case["pdf_years"]:
        with open(_pdf_path(municipality, year), "rb") as handle:
            parsed, unmatched = affaldonline._parse_calendar_pdf(
                handle.read(), fallback_year=int(year)
            )
        assert unmatched == 0
        assert len({day for day, _label in parsed}) > 1


@pytest.mark.parametrize("municipality", sorted(NO_COLLECTION_DATES))
def test_sample_without_collection_dates_uses_next_emptying_line(municipality):
    case = CALENDAR_CASES[municipality]
    source, session = _fetch_recorded_case(municipality)
    with pytest.raises(ValueError, match="No waste schemes found"):
        source.fetch()

    soup = affaldonline.BeautifulSoup(case["show_info"], "html.parser")
    assert affaldonline._calendar_buttons(soup)
    assert affaldonline._next_emptying_dates(soup) == set()
    assert affaldonline._parse_next(soup) == []
    buttons = affaldonline._calendar_buttons(soup)
    following = affaldonline._following_year_button(buttons)
    assert [params for _url, params in session.get_calls] == [*buttons, following]
    assert session.posted == [{"values": SAMPLE_VALUES[municipality]}]

    with open(_pdf_path(municipality, "2026"), "rb") as handle:
        pdf_bytes = handle.read()
    parsed, unmatched = affaldonline._parse_calendar_pdf(pdf_bytes, fallback_year=2026)
    assert parsed == []
    assert unmatched == 0
    assert NO_COLLECTION_DATES[municipality] in extract_text(BytesIO(pdf_bytes))
