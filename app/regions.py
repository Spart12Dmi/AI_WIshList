from dataclasses import dataclass


@dataclass(frozen=True)
class RegionProfile:
    key: str
    label: str
    search_region: str
    search_hint: str
    language: str
    shopping_terms: str
    preferred_currencies: tuple[str, ...]
    country_domains: tuple[str, ...]


REGIONS: dict[str, RegionProfile] = {
    "czechia": RegionProfile(
        "czechia",
        "Czechia",
        "cz-cs",
        "Czech Republic Česko",
        "Czech",
        "produkt cena koupit",
        ("CZK",),
        (".cz",),
    ),
    "slovakia": RegionProfile(
        "slovakia",
        "Slovakia",
        "sk-sk",
        "Slovakia Slovensko",
        "Slovak",
        "produkt cena kúpiť",
        ("EUR",),
        (".sk",),
    ),
    "poland": RegionProfile(
        "poland", "Poland", "pl-pl", "Poland Polska", "Polish", "produkt cena kup", ("PLN",), (".pl",)
    ),
    "germany": RegionProfile(
        "germany",
        "Germany",
        "de-de",
        "Germany Deutschland",
        "German",
        "Produkt Preis kaufen",
        ("EUR",),
        (".de",),
    ),
    "austria": RegionProfile(
        "austria",
        "Austria",
        "at-de",
        "Austria Österreich",
        "German",
        "Produkt Preis kaufen",
        ("EUR",),
        (".at",),
    ),
    "united_kingdom": RegionProfile(
        "united_kingdom",
        "United Kingdom",
        "uk-en",
        "United Kingdom UK",
        "English",
        "product price buy",
        ("GBP",),
        (".uk",),
    ),
    "united_states": RegionProfile(
        "united_states",
        "United States",
        "us-en",
        "United States USA",
        "English",
        "product price buy",
        ("USD",),
        (".us",),
    ),
    "global": RegionProfile(
        "global", "Global / any region", "wt-wt", "", "the local language", "product price buy", (), ()
    ),
}


def get_region(key: str) -> RegionProfile:
    return REGIONS[key]
