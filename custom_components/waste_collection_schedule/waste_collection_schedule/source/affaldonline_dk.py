"""Affaldonline, via the shared 3.0 pipeline.

The address page is fetched first. When it links a ``showToemCal`` year
calendar, every pickup in that PDF becomes a collection. The old
"Næste tømningsdag" page parser runs only when that PDF cannot be read.
"""

from typing import ClassVar, final

from waste_collection_schedule import waste_types as wt
from waste_collection_schedule.base_source import BaseSource
from waste_collection_schedule.config_params import (
    alternatives,
    city,
    house_number,
    municipality,
    street,
    text_field,
)
from waste_collection_schedule.field_terms import POSTCODE, as_text
from waste_collection_schedule.parsers import FirstNonEmptyBranch
from waste_collection_schedule.regions import Region
from waste_collection_schedule.retrievers import (
    Branch,
    FallbackRetriever,
    reuse_prepared,
)
from waste_collection_schedule.service.Affaldonline import (
    MUNICIPALITIES,
    AffaldonlineCalendarParser,
    AffaldonlineCalendars,
    AffaldonlineHtmlParser,
    AffaldonlinePage,
)
from waste_collection_schedule.transformers import ICSTransformer

# Labels the shared vocabulary does not already classify. Combined rounds stay
# one collection; the provider's own wording is kept on the description.
_TYPE_MAP = {
    "Rest/Mad": wt.GENERAL_WASTE,
    "Mad/Rest": wt.GENERAL_WASTE,
    "Rest-/madaffald": wt.GENERAL_WASTE,
    "Restaffald": wt.GENERAL_WASTE,
    "Dagrenovation": wt.GENERAL_WASTE,
    "Bioaffald": wt.FOOD_WASTE,
    "Pap/Papir": wt.PAPER,
    "Papir/Pap": wt.PAPER,
    "PPGM": wt.RECYCLABLES,
    "Papir/Pap/Glas": wt.RECYCLABLES,
    "Papir/Pap og tekstil": wt.RECYCLABLES,
    "Papir/Pap-Metal/Glas": wt.RECYCLABLES,
    "Papir/Pap og Plast/Mad- og drikkekartoner": wt.RECYCLABLES,
    "Papir/småt pap og glas/metal": wt.RECYCLABLES,
    "Pap/papir og glas/metal": wt.RECYCLABLES,
    "Glas og metal": wt.GLASS,
    "Metal": wt.RECYCLABLES,
    "PMDK": wt.RECYCLABLES,
    "Plast/MDK": wt.RECYCLABLES,
    "Plast/Drikkekarton": wt.RECYCLABLES,
    "Plast/Drikkekarton/Metal": wt.RECYCLABLES,
    "Plast/fødevarekarton": wt.RECYCLABLES,
    "Plast + Mad-/Drikkekartoner": wt.RECYCLABLES,
    "Plast/mad- og drikkekartoner og glas/metal": wt.RECYCLABLES,
    "Metal/Glas/Plast/MDK": wt.RECYCLABLES,
    "Plast og Drikkekartoner": wt.RECYCLABLES,
    "Pap/Papir og Glas/Metal": wt.RECYCLABLES,
    "Genbrug": wt.RECYCLABLES,
    "Genanvendeligt": wt.RECYCLABLES,
    "Miljøkasse": wt.HAZARDOUS,
    "Røde kasser": wt.HAZARDOUS,
    "Tekstilaffald": wt.TEXTILES,
}

# Public Holbæk example used by the offline calendar fixture.
_HOLBAEK_VALUES = "Tåstrup Møllevej|5||||4300|Holbæk|76490500|1676484|0"


def _regions() -> list[Region]:
    return [
        Region(
            title=info["title"],
            url=info["url"],
            country="dk",
            params={"municipality": key},
        )
        for key, info in MUNICIPALITIES.items()
    ]


@final
class Source(BaseSource):
    TITLE = "Affaldonline"
    DESCRIPTION = "Waste collections from Affaldonline municipalities."
    URL = "https://affaldonline.dk"
    COUNTRY = "dk"
    SOURCE_CODEOWNERS: ClassVar[list] = ["@superrob"]

    TEST_CASES: ClassVar[dict] = {
        "holbaek": {"municipality": "holbaek", "values": _HOLBAEK_VALUES},
    }

    PARAMS = (
        municipality(),
        alternatives(
            [text_field("values", "Values string")],
            [street(), house_number()],
        ),
        text_field("postal_code", term=POSTCODE, optional=True, coerce=as_text),
        city(optional=True),
    )

    WASTE_TYPES: ClassVar[list] = [
        wt.GENERAL_WASTE,
        wt.FOOD_WASTE,
        wt.PAPER,
        wt.GLASS,
        wt.RECYCLABLES,
        wt.GARDEN_WASTE,
        wt.BULKY_WASTE,
        wt.HAZARDOUS,
        wt.TEXTILES,
    ]

    HOWTO: ClassVar[dict] = {
        "en": (
            "Select the municipality. Then either paste the values string from "
            "the house-number field on that municipality's Affaldonline calendar, "
            "or enter the street and house number. Postal code and city disambiguate "
            "a street name that exists in more than one town. When the address page "
            "links a year calendar, every date in it is imported; otherwise only the "
            "next collection date printed on the page is used."
        ),
    }

    REGIONS = _regions
    RAISE_ON_EMPTY = True

    retrieve = FallbackRetriever(
        Branch("calendar", AffaldonlineCalendars()),
        Branch("page", reuse_prepared),
        prepare=AffaldonlinePage(),
    )
    parse = FirstNonEmptyBranch(
        {
            "calendar": AffaldonlineCalendarParser(),
            "page": AffaldonlineHtmlParser(),
        }
    )
    transform = ICSTransformer(_TYPE_MAP, carry_raw_label=True)
