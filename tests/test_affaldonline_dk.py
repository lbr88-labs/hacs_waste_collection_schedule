import calendar as _stdlib_calendar  # noqa: F401
import os
import sys
from collections import defaultdict
from datetime import date

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
