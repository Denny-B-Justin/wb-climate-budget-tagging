"""
Rio Convention Marker keyword dictionaries for green budget tagging.

This module contains the keyword lists used for Rio Marker tagging
following the Rio Convention/Paris Agreement framework.
"""

# =============================================================================
# MITIGATION KEYWORDS
# =============================================================================
# Activities that reduce greenhouse gas emissions or remove carbon from atmosphere

MITIGATION_KEYWORDS = [
    "renewable",
    "solar",
    "wind",
    "hydro",
    "energy efficiency",
    "low carbon",
    "electric vehicle",
    "ev charging",
    "charging infrastructure",
    "emission reduction",
    "decarbonization",
    "decarbonisation",
    "green hydrogen",
    "battery storage",
    "carbon capture",
    "clean energy",
    "renewable energy",
    "carbon offset",
    "carbon neutral",
    "net zero",
    "bioenergy",
    "geothermal",
    "biomass",
    "energy transition",
    "energy retrofits",
    "insulation",
    "heat pump",
    "smart grid",
    "energy storage",
]

# =============================================================================
# ADAPTATION KEYWORDS
# =============================================================================
# Activities that reduce vulnerability and increase resilience to climate impacts

ADAPTATION_KEYWORDS = [
    "flood",
    "flood control",
    "flood protection",
    "flood defense",
    "flood wall",
    "levee",
    "drought",
    "climate resilient",
    "resilience",
    "resilient",
    "climate resilience",
    "climate proof",
    "storm water",
    "coastal protection",
    "coastline protection",
    "sea level rise",
    "coastal erosion",
    "early warning system",
    "early warning",
    "heatwave",
    "heat wave",
    "heat stress",
    "disaster risk reduction",
    "disaster prevention",
    "climate adaptation",
    "adaptation measure",
    "water management",
    "irrigation",
    "water conservation",
    "agricultural resilience",
    "ecosystem restoration",
    "reforestation",
    "afforestation",
    "mangrove restoration",
]

# =============================================================================
# EXPLICIT CLIMATE TERMS
# =============================================================================
# Terms that explicitly reference climate change, mitigation, or adaptation

EXPLICIT_CLIMATE_TERMS = [
    "climate change",
    "global warming",
    "mitigation",
    "adaptation",
    "paris agreement",
    "paris agreement",
    "ndc",
    "nationally determined contribution",
    "climate action",
    "climate policy",
    "climate finance",
    "climate fund",
    "greenhouse gas",
    "ghg",
    "co2",
    "carbon emission",
    "climate target",
]

# =============================================================================
# EXCLUSION TERMS
# =============================================================================
# Terms that indicate administrative or non-climate expenditures (conservative tagging)

EXCLUSION_TERMS = [
    "general administration",
    "office equipment",
    "office supplies",
    "salary",
    "pension",
    "personnel costs",
    "debt service",
    "interest payment",
    "legal services",
    "audit services",
    "routine maintenance",
    "general operations",
    "administrative overhead",
]

# =============================================================================
# RIO MARKER VALUES
# =============================================================================

RIO_MARKER_NOT_TARGETED = 0  # No clear climate relevance
RIO_MARKER_SIGNIFICANT = 1  # Climate is one of several objectives
RIO_MARKER_PRINCIPAL = 2  # Climate is the main purpose

RIO_MARKER_LABELS = {
    RIO_MARKER_NOT_TARGETED: "Not Targeted",
    RIO_MARKER_SIGNIFICANT: "Significant Objective",
    RIO_MARKER_PRINCIPAL: "Principal Objective",
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_mitigation_keywords():
    """Return list of mitigation keywords."""
    return MITIGATION_KEYWORDS


def get_adaptation_keywords():
    """Return list of adaptation keywords."""
    return ADAPTATION_KEYWORDS


def get_explicit_climate_terms():
    """Return list of explicit climate terms."""
    return EXPLICIT_CLIMATE_TERMS


def get_exclusion_terms():
    """Return list of exclusion terms."""
    return EXCLUSION_TERMS


def get_rio_marker_label(marker_value: int) -> str:
    """Convert Rio Marker value to human-readable label."""
    return RIO_MARKER_LABELS.get(marker_value, str(marker_value))
