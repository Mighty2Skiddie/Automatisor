"""The sector registry.

Sector is switchable independently of persona, so every persona x sector pair is
valid — four sectors and three personas give twelve combinations.

Four sectors, not the three the build plan names. The assessment says "pick any 3"
but its own worked example and its only API-specific test both use Logistics, while
its other sample questions name Tech, Retail and Manufacturing. Shipping four means
every question in the brief runs verbatim; an unknown sector still returns a 422
listing what is available.

**Nothing in this module may contain a company name, a ticker or a figure.** The
sector context is framing — what kind of business this is and which economics tend
to matter — and it goes into the system prompt. If a fact lived here it would be a
fact the agent could state without querying the database, which is precisely what
the no-hardcoded-facts rule forbids. ``tests/test_personas.py`` enforces it by
rejecting any digit or ticker-shaped token in the text below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Sector:
    """One sector's identity and analytical framing."""

    key: str
    label: str
    description: str
    what_drives_it: str


TECH: Final[Sector] = Sector(
    key="tech",
    label="Technology",
    description=(
        "Large-cap software, semiconductors and internet platforms. Asset-light "
        "businesses where the value sits in intangibles rather than plant."
    ),
    what_drives_it=(
        "Gross margins run high and vary widely between software and hardware, so "
        "compare margin structure within the sector rather than against it. Growth "
        "rates disperse more than in any other sector here, capital intensity is "
        "generally low, and valuations carry expectations that make the multiple a "
        "live question rather than a footnote."
    ),
)

RETAIL: Final[Sector] = Sector(
    key="retail",
    label="Retail",
    description=(
        "Mass-market merchants: big-box, grocery, discount, home improvement and "
        "off-price. High-volume, thin-margin businesses built on inventory turns."
    ),
    what_drives_it=(
        "Operating margins are structurally thin, so small differences are material "
        "and a comparison against another sector's margins is meaningless. Scale and "
        "inventory efficiency separate the winners, leases make reported leverage "
        "look heavier than the operating reality, and demand is tied to the consumer "
        "cycle."
    ),
)

MANUFACTURING: Final[Sector] = Sector(
    key="manufacturing",
    label="Manufacturing",
    description=(
        "Industrial and capital-goods producers: machinery, electrical equipment, "
        "automation and diversified industrials. Capital-intensive and cyclical."
    ),
    what_drives_it=(
        "Heavy fixed assets make margins swing with volume, so the same company looks "
        "very different at two points in the cycle. Backlog and aftermarket service "
        "mix drive earnings quality, capital intensity keeps free cash flow well below "
        "reported earnings, and balance sheets carry real debt."
    ),
)

LOGISTICS: Final[Sector] = Sector(
    key="logistics",
    label="Logistics & Transportation",
    description=(
        "Freight and delivery networks: parcel, less-than-truckload, rail, and asset-"
        "light freight brokerage. Network businesses where density drives economics."
    ),
    what_drives_it=(
        "The sector splits sharply between asset-heavy network operators and asset-"
        "light brokers, and the two have completely different margin and capital "
        "profiles — never compare them without saying which is which. Network density "
        "and utilisation drive unit economics, the asset-heavy names carry substantial "
        "debt and depreciation, and volumes track industrial and consumer activity."
    ),
)

SECTORS: Final[dict[str, Sector]] = {
    sector.key: sector
    for sector in (TECH, RETAIL, MANUFACTURING, LOGISTICS)
}

SECTOR_KEYS: Final[tuple[str, ...]] = tuple(SECTORS)


class UnknownSectorError(ValueError):
    """Raised for a sector key outside the registry."""


def get_sector(key: str) -> Sector:
    """Look up a sector, failing with the valid values rather than a KeyError.

    The message is user-facing: it becomes the 422 body when the API is asked for a
    sector that does not exist, which is exactly what the assessment's own API
    example does. A clear list of what *is* available beats a 500 or an invented
    answer.
    """
    normalised = key.strip().lower()
    if normalised not in SECTORS:
        raise UnknownSectorError(
            f"Unknown sector '{key}'. Valid: {', '.join(SECTOR_KEYS)}"
        )
    return SECTORS[normalised]


def is_valid_sector(key: str) -> bool:
    """Whether a sector exists, without raising."""
    return key.strip().lower() in SECTORS
