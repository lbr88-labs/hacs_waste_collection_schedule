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
