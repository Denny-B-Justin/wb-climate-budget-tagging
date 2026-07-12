"""
EU Taxonomy Keywords

Keywords for the 6 EU Environmental Objectives:
1. Climate Change Mitigation
2. Climate Change Adaptation
3. Sustainable Use and Protection of Water and Marine Resources
4. Transition to a Circular Economy
5. Pollution Prevention and Control
6. Protection and Restoration of Biodiversity and Ecosystems
"""

# 1. Climate Change Mitigation
MITIGATION_KEYWORDS = [
    "renewable energy", "solar", "wind", "hydroelectric", "geothermal", "biomass",
    "energy efficiency", "insulation", "led lighting", "smart grid",
    "electric vehicle", "ev charging", "public transport", "railway", "metro", "tram",
    "bicycle", "cycling", "carbon capture", "afforestation", "reforestation",
    "low carbon", "decarbonization", "methane reduction", "green hydrogen"
]

# 2. Climate Change Adaptation
ADAPTATION_KEYWORDS = [
    "climate resilience", "climate adaptation", "flood defense", "flood protection",
    "sea wall", "storm surge", "early warning system", "drought resilience",
    "water conservation", "irrigation efficiency", "crop diversification",
    "heatwave management", "urban cooling", "green roof", "permeable pavement",
    "disaster risk reduction", "resilient infrastructure", "coastal protection"
]

# 3. Sustainable Use and Protection of Water and Marine Resources
WATER_KEYWORDS = [
    "water treatment", "wastewater", "sewage", "water quality", "desalination",
    "marine protection", "ocean conservation", "aquatic ecosystem", "river restoration",
    "wetland", "water efficiency", "leak detection", "sustainable fishing",
    "aquaculture", "coastal management", "groundwater"
]

# 4. Transition to a Circular Economy
CIRCULAR_ECONOMY_KEYWORDS = [
    "circular economy", "recycling", "waste management", "waste reduction",
    "composting", "refurbishment", "remanufacturing", "resource efficiency",
    "upcycling", "extended producer responsibility", "zero waste", "bio-based materials",
    "sustainable packaging", "product longevity", "shared economy"
]

# 5. Pollution Prevention and Control
POLLUTION_KEYWORDS = [
    "pollution control", "air quality", "emissions reduction", "noise reduction",
    "decontamination", "soil remediation", "hazardous waste", "industrial emissions",
    "chemical safety", "plastic pollution", "pesticide reduction", "clean air",
    "smog", "particulate matter", "voc reduction"
]

# 6. Protection and Restoration of Biodiversity and Ecosystems
BIODIVERSITY_KEYWORDS = [
    "biodiversity", "ecosystem restoration", "nature reserve", "national park",
    "wildlife conservation", "habitat protection", "forest management", "tree planting",
    "pollinator", "invasive species", "protected area", "ecological corridor",
    "soils health", "organic farming", "permaculture", "natural capital"
]

# 7. Brown / Environmentally Harmful
BROWN_KEYWORDS = [
    "fossil fuel", "coal", "oil extraction", "natural gas", "crude oil",
    "thermal power", "gasoline", "diesel", "aviation fuel", "heavy industry",
    "deforestation", "mining", "quarrying", "plastic production", "cement production",
    "petrochemical", "incineration", "landfill", "industrial pollution"
]

# Unified dictionary for easy access
EU_OBJECTIVES_KEYWORDS = {
    "mitigation": MITIGATION_KEYWORDS,
    "adaptation": ADAPTATION_KEYWORDS,
    "water": WATER_KEYWORDS,
    "circular_economy": CIRCULAR_ECONOMY_KEYWORDS,
    "pollution": POLLUTION_KEYWORDS,
    "biodiversity": BIODIVERSITY_KEYWORDS
}

# Labels for UI
EU_OBJECTIVE_LABELS = {
    "mitigation": "Climate Change Mitigation",
    "adaptation": "Climate Change Adaptation",
    "water": "Sustainable Water & Marine Resources",
    "circular_economy": "Transition to a Circular Economy",
    "pollution": "Pollution Prevention & Control",
    "biodiversity": "Biodiversity & Ecosystems"
}

def get_eu_objective_label(objective_key: str) -> str:
    """Return human-readable label for an objective key."""
    return EU_OBJECTIVE_LABELS.get(objective_key, "Unknown Objective")
