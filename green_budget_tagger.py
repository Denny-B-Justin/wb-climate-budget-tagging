"""
Green Budget Tagging System
===========================
A Python script for automated green budget tagging of World Bank BOOST data.

This implements:
1. Phase 0: COFOG-based rule tagging (deterministic)
2. Phase 1: LLM-based tagging for ambiguous cases (using Claude/OpenAI)

Usage:
    python green_budget_tagger.py input.xlsx --output tagged_output.xlsx
    python green_budget_tagger.py input.xlsx --output tagged_output.xlsx --use-llm --api-key YOUR_KEY
"""

import pandas as pd
import re
import json
import argparse
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ClimateTag(Enum):
    """Climate classification tags"""
    GREEN = "GREEN"
    NEUTRAL = "NEUTRAL"
    BROWN = "BROWN"
    AMBIGUOUS = "AMBIGUOUS"


class Confidence(Enum):
    """Confidence levels for classifications"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EnvironmentalObjective(Enum):
    """Environmental objectives based on OECD framework"""
    MITIGATION = "mitigation"
    ADAPTATION = "adaptation"
    BIODIVERSITY = "biodiversity"
    WATER = "water"
    WASTE = "waste"
    POLLUTION = "pollution"
    CIRCULAR_ECONOMY = "circular_economy"
    NONE = "none"


@dataclass
class TaggingResult:
    """Result of tagging a budget line"""
    climate_tag: ClimateTag
    confidence: Confidence
    method: str
    environmental_objective: EnvironmentalObjective = EnvironmentalObjective.NONE
    subcategory: Optional[str] = None  # 'principal', 'significant', or None
    rationale: str = ""


# =============================================================================
# COFOG-BASED TAGGING RULES (Phase 0)
# =============================================================================

# COFOG func1 code mappings to default climate tags
FUNC1_DEFAULT_TAGS: Dict[str, ClimateTag] = {
    # CLEAR GREEN - Environmental protection activities
    "0500": ClimateTag.GREEN,
    "05": ClimateTag.GREEN,
    "environmental protection": ClimateTag.GREEN,

    # CLEAR NEUTRAL - Non-climate relevant sectors
    "0100": ClimateTag.NEUTRAL,  # General public services
    "01": ClimateTag.NEUTRAL,
    "general public services": ClimateTag.NEUTRAL,

    "0200": ClimateTag.NEUTRAL,  # Defense
    "02": ClimateTag.NEUTRAL,
    "defense": ClimateTag.NEUTRAL,
    "defence": ClimateTag.NEUTRAL,

    "0300": ClimateTag.NEUTRAL,  # Public order and safety
    "03": ClimateTag.NEUTRAL,
    "public order": ClimateTag.NEUTRAL,

    "0700": ClimateTag.NEUTRAL,  # Healthcare
    "07": ClimateTag.NEUTRAL,
    "health": ClimateTag.NEUTRAL,
    "healthcare": ClimateTag.NEUTRAL,

    "0900": ClimateTag.NEUTRAL,  # Education
    "09": ClimateTag.NEUTRAL,
    "education": ClimateTag.NEUTRAL,

    "1000": ClimateTag.NEUTRAL,  # Social protection
    "10": ClimateTag.NEUTRAL,
    "social protection": ClimateTag.NEUTRAL,

    # AMBIGUOUS - Requires further analysis
    "0400": ClimateTag.AMBIGUOUS,  # Economic affairs (could be renewable OR fossil)
    "04": ClimateTag.AMBIGUOUS,
    "economic": ClimateTag.AMBIGUOUS,

    "0600": ClimateTag.AMBIGUOUS,  # Housing and community amenities
    "06": ClimateTag.AMBIGUOUS,
    "housing": ClimateTag.AMBIGUOUS,

    "0800": ClimateTag.AMBIGUOUS,  # Recreation, culture, religion
    "08": ClimateTag.AMBIGUOUS,
    "recreation": ClimateTag.AMBIGUOUS,
}

# Economic classification signals
ECON_SIGNALS: Dict[str, str] = {
    # Capital expenditures - potential for green infrastructure
    "3100": "POTENTIAL_GREEN",  # Acquisition of capital assets
    "31": "POTENTIAL_GREEN",
    "capital": "POTENTIAL_GREEN",
    "3200": "POTENTIAL_GREEN",  # Capital transfers
    "32": "POTENTIAL_GREEN",

    # Current transfers - usually neutral
    "2700": "NEUTRAL",  # Social security
    "27": "NEUTRAL",
    "social": "NEUTRAL",

    # Debt service - always neutral
    "2400": "NEUTRAL",  # Interest payments
    "24": "NEUTRAL",
    "interest": "NEUTRAL",
    "debt": "NEUTRAL",
}

# Green keywords (English and Ukrainian)
GREEN_KEYWORDS: List[str] = [
    # Energy
    "renewable", "solar", "wind", "hydro", "geothermal", "biomass",
    "energy efficiency", "clean energy", "green energy",
    "відновлювальна", "сонячна", "вітрова", "енергоефективність",

    # Transport
    "public transport", "electric vehicle", "ev charging", "cycling",
    "pedestrian", "low carbon transport", "rail", "metro", "tram",
    "громадський транспорт", "електротранспорт",

    # Environment
    "environment", "ecology", "conservation", "biodiversity",
    "ecosystem", "nature reserve", "protected area", "reforestation",
    "afforestation", "forest", "wetland",
    "екологія", "довкілля", "природа", "заповідник",

    # Water
    "water treatment", "wastewater", "sewage", "clean water",
    "water management", "flood", "drainage",
    "вода", "водні ресурси", "очистка води",

    # Waste
    "waste management", "recycling", "circular economy", "composting",
    "відходи", "переробка", "рециклінг",

    # Climate
    "climate", "carbon", "emissions reduction", "net zero",
    "adaptation", "resilience", "climate change",
    "клімат", "зміна клімату", "адаптація",

    # Buildings
    "green building", "insulation", "energy audit", "retrofit",
    "sustainable construction",
]

# Brown keywords (environmentally harmful)
BROWN_KEYWORDS: List[str] = [
    # Fossil fuels
    "coal", "oil", "petroleum", "natural gas", "fossil fuel",
    "coal mining", "oil extraction", "gas extraction",
    "вугілля", "нафта", "газ", "паливо",

    # Harmful subsidies
    "fossil fuel subsidy", "coal subsidy", "oil subsidy",

    # Carbon intensive
    "cement production", "steel production", "aluminum smelting",

    # Harmful infrastructure
    "highway expansion", "airport expansion",
]

# Exclusion keywords (these override green keywords in context)
EXCLUSION_CONTEXTS: List[str] = [
    "administrative", "salary", "wages", "personnel",
    "office supplies", "stationery", "furniture",
]


class WordProcessor:
    """
    Process Word documents with budget data.
    Extracts text and uses LLM to convert to structured DataFrame.
    """
    def __init__(self, file_path: str, api_key: str = None):
        self.file_path = Path(file_path)
        self.api_key = api_key
        self.text_content = ""

    def load_text(self) -> str:
        """Extract all text from Word document"""
        try:
            from docx import Document
            doc = Document(self.file_path)
            full_text = []
            for para in doc.paragraphs:
                full_text.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        full_text.append(cell.text)
            self.text_content = "\n".join(full_text)
            return self.text_content
        except ImportError:
            logger.error("python-docx not installed. Run: pip install python-docx")
            return ""
        except Exception as e:
            logger.error(f"Failed to load Word file {self.file_path}: {e}")
            return ""

    def to_dataframe(self) -> pd.DataFrame:
        """Use LLM to convert unstructured text to structured budget DataFrame"""
        if not self.text_content:
            self.load_text()
            
        if not self.text_content:
            return pd.DataFrame()

        if not self.api_key:
            logger.warning("No API key provided for Word conversion. Returning empty DataFrame.")
            return pd.DataFrame()

        # Simple prompt for LLM to extract data
        prompt = f"""
        Extract budget line items from the following text into a structured JSON list.
        Each item should have:
        - func1: Functional classification (sector)
        - prog1: Program or project name
        - amount: Budget amount (numeric only)
        - year: Fiscal year
        
        Text:
        {self.text_content[:4000]}  # Limit text to avoid token issues
        
        Return ONLY a JSON list of objects.
        """
        
        try:
            # Use LLMTagger to get JSON
            tagger = LLMTagger(api_key=self.api_key)
            response = tagger._call_anthropic(prompt) if tagger.provider == "anthropic" else tagger._call_openai(prompt)
            
            import json
            # Extract JSON from response if needed
            if "```json" in response:
                response = response.split("```json")[-1].split("```")[0]
            elif "[" in response:
                response = response[response.find("["):response.rfind("]")+1]
                
            data = json.loads(response)
            df = pd.DataFrame(data)
            
            # Ensure mandatory columns exist
            for col in ["func1", "prog1", "amount", "year"]:
                if col not in df.columns:
                    df[col] = ""
            
            # Auto-detect mapping
            self.column_mapping = {col: col for col in df.columns}
            
            return df
        except Exception as e:
            logger.error(f"LLM conversion failed: {e}")
            return pd.DataFrame()


class COFOGTagger:
    """
    COFOG-based rule tagger for Phase 0 implementation.
    Uses functional classification codes and keywords to tag budget lines.
    """

    def __init__(self, mapping_path: Optional[str] = None):
        self.func1_tags = FUNC1_DEFAULT_TAGS
        self.econ_signals = ECON_SIGNALS
        self.green_keywords = GREEN_KEYWORDS
        self.brown_keywords = BROWN_KEYWORDS
        self.exclusion_contexts = EXCLUSION_CONTEXTS
        
        # Load predetermined mapping if provided
        self.predetermined_mapping = {}
        if mapping_path:
            try:
                with open(mapping_path, 'r') as f:
                    self.predetermined_mapping = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load COFOG mapping from {mapping_path}: {e}")

    def get_predetermined_tag(self, func1: str, method: str) -> Optional[Dict[str, Any]]:
        """
        Get predetermined tag based on COFOG code for a specific method (rio or eu).
        
        Args:
            func1: Functional classification string
            method: 'rio' or 'eu'
            
        Returns:
            Dictionary with mapping results or None
        """
        code = self._extract_code(self._normalize_text(func1))
        if not code or method not in self.predetermined_mapping:
            return None
            
        return self.predetermined_mapping[method].get(code)

    def _normalize_text(self, text: Any) -> str:
        """Normalize text for comparison"""
        if pd.isna(text):
            return ""
        return str(text).lower().strip()

    def _extract_code(self, text: str) -> str:
        """Extract numeric code from classification string"""
        if not text:
            return ""
        # Match patterns like "0500", "05", "0500 Environmental protection"
        match = re.match(r'^(\d{2,4})', text)
        return match.group(1) if match else ""

    def _check_keywords(self, text: str, keywords: List[str]) -> Tuple[bool, List[str]]:
        """Check if text contains any keywords, return matches"""
        text_lower = self._normalize_text(text)
        matches = [kw for kw in keywords if kw.lower() in text_lower]
        return len(matches) > 0, matches

    def _check_exclusions(self, text: str) -> bool:
        """Check if text contains exclusion context"""
        text_lower = self._normalize_text(text)
        return any(exc.lower() in text_lower for exc in self.exclusion_contexts)

    def tag_by_func1(self, func1: str) -> Optional[TaggingResult]:
        """Tag based on func1 classification code"""
        func1_norm = self._normalize_text(func1)
        code = self._extract_code(func1_norm)

        # Check by code first
        if code in self.func1_tags:
            tag = self.func1_tags[code]
            if tag == ClimateTag.AMBIGUOUS:
                return None  # Needs further analysis
            return TaggingResult(
                climate_tag=tag,
                confidence=Confidence.HIGH,
                method="COFOG_FUNC1_CODE",
                rationale=f"Classified by COFOG func1 code: {code}"
            )

        # Check by text content
        for key, tag in self.func1_tags.items():
            if key in func1_norm and not key.isdigit():
                if tag == ClimateTag.AMBIGUOUS:
                    return None
                return TaggingResult(
                    climate_tag=tag,
                    confidence=Confidence.HIGH,
                    method="COFOG_FUNC1_TEXT",
                    rationale=f"Classified by COFOG func1 text match: {key}"
                )

        return None

    def tag_by_keywords(self, row: pd.Series, text_columns: List[str]) -> Optional[TaggingResult]:
        """Tag based on keyword analysis in program descriptions"""
        combined_text = " ".join(
            self._normalize_text(row.get(col, ""))
            for col in text_columns
        )

        # Check for exclusion contexts first
        if self._check_exclusions(combined_text):
            return None  # Skip keyword tagging for administrative items

        # Check for brown keywords
        has_brown, brown_matches = self._check_keywords(combined_text, self.brown_keywords)
        if has_brown:
            return TaggingResult(
                climate_tag=ClimateTag.BROWN,
                confidence=Confidence.MEDIUM,
                method="KEYWORD_BROWN",
                environmental_objective=EnvironmentalObjective.NONE,
                rationale=f"Brown keywords found: {', '.join(brown_matches[:3])}"
            )

        # Check for green keywords
        has_green, green_matches = self._check_keywords(combined_text, self.green_keywords)
        if has_green:
            # Determine environmental objective
            objective = self._determine_objective(green_matches)
            return TaggingResult(
                climate_tag=ClimateTag.GREEN,
                confidence=Confidence.MEDIUM,
                method="KEYWORD_GREEN",
                environmental_objective=objective,
                subcategory="significant",
                rationale=f"Green keywords found: {', '.join(green_matches[:3])}"
            )

        return None

    def _determine_objective(self, keywords: List[str]) -> EnvironmentalObjective:
        """Determine environmental objective from matched keywords"""
        keywords_lower = [k.lower() for k in keywords]

        if any(k in keywords_lower for k in ["climate", "carbon", "emission", "mitigation"]):
            return EnvironmentalObjective.MITIGATION
        if any(k in keywords_lower for k in ["adaptation", "resilience", "flood"]):
            return EnvironmentalObjective.ADAPTATION
        if any(k in keywords_lower for k in ["biodiversity", "ecosystem", "forest", "nature"]):
            return EnvironmentalObjective.BIODIVERSITY
        if any(k in keywords_lower for k in ["water", "wastewater", "sewage"]):
            return EnvironmentalObjective.WATER
        if any(k in keywords_lower for k in ["waste", "recycling", "circular"]):
            return EnvironmentalObjective.WASTE
        if any(k in keywords_lower for k in ["pollution", "air quality"]):
            return EnvironmentalObjective.POLLUTION

        return EnvironmentalObjective.MITIGATION  # Default for energy/transport

    def tag_by_econ_func_combination(
        self,
        func1: str,
        econ1: str,
        econ2: str
    ) -> Optional[TaggingResult]:
        """Tag based on economic + functional classification combination"""
        func1_norm = self._normalize_text(func1)
        econ1_norm = self._normalize_text(econ1)
        econ2_norm = self._normalize_text(econ2)

        func_code = self._extract_code(func1_norm)
        econ1_code = self._extract_code(econ1_norm)
        econ2_code = self._extract_code(econ2_norm)

        # Capital expenditure in economic affairs sector - needs LLM
        if func_code in ["04", "0400"] and econ1_code in ["31", "3100", "32", "3200"]:
            return TaggingResult(
                climate_tag=ClimateTag.AMBIGUOUS,
                confidence=Confidence.LOW,
                method="ECON_FUNC_COMBO",
                rationale="Capital expenditure in economic sector - requires detailed analysis"
            )

        # Environmental protection with capital expenditure - likely principal green
        if func_code in ["05", "0500"] and econ1_code in ["31", "3100", "32", "3200"]:
            return TaggingResult(
                climate_tag=ClimateTag.GREEN,
                confidence=Confidence.HIGH,
                method="ECON_FUNC_COMBO",
                environmental_objective=EnvironmentalObjective.MITIGATION,
                subcategory="principal",
                rationale="Capital investment in environmental protection"
            )

        return None

    def tag_row(self, row: pd.Series, column_mapping: Dict[str, str]) -> TaggingResult:
        """
        Apply COFOG-based tagging to a single row.

        Args:
            row: DataFrame row
            column_mapping: Mapping of standard names to actual column names

        Returns:
            TaggingResult with classification
        """
        # Get column values using mapping
        func1 = row.get(column_mapping.get("func1", ""), "")
        func2 = row.get(column_mapping.get("func2", ""), "")
        econ1 = row.get(column_mapping.get("econ1", ""), "")
        econ2 = row.get(column_mapping.get("econ2", ""), "")

        # Get all text columns for keyword analysis
        text_columns = [
            column_mapping.get(col, "")
            for col in ["prog1", "prog2", "func2", "func3", "func4"]
            if column_mapping.get(col)
        ]

        # Step 1: Check func1 classification
        result = self.tag_by_func1(func1)
        if result and result.climate_tag != ClimateTag.AMBIGUOUS:
            return result

        # Step 2: Check economic + functional combination
        result = self.tag_by_econ_func_combination(func1, econ1, econ2)
        if result and result.climate_tag != ClimateTag.AMBIGUOUS:
            return result

        # Step 3: Check keywords in program descriptions
        result = self.tag_by_keywords(row, text_columns)
        if result:
            return result

        # Step 4: Default to neutral for unclear cases
        return TaggingResult(
            climate_tag=ClimateTag.NEUTRAL,
            confidence=Confidence.LOW,
            method="DEFAULT",
            rationale="No clear climate relevance identified"
        )


# =============================================================================
# LLM-BASED TAGGING (Phase 1)
# =============================================================================

LLM_SYSTEM_PROMPT = """You are an expert in climate budget tagging working with government expenditure data.
Your task is to classify budget line items as GREEN, NEUTRAL, or BROWN based on their environmental/climate impact.

DEFINITIONS:
- GREEN: Expenditures that contribute to climate mitigation, adaptation, or environmental protection
  * Examples: Renewable energy, public transport, flood defenses, ecosystem restoration, energy efficiency

- NEUTRAL: Expenditures with no significant environmental impact (positive or negative)
  * Examples: Administrative salaries, social transfers, routine maintenance, healthcare, education (unless climate-related)

- BROWN: Expenditures that undermine climate goals or harm the environment
  * Examples: Fossil fuel subsidies, coal power plants, environmentally destructive infrastructure

ENVIRONMENTAL OBJECTIVES (for GREEN items):
- mitigation: Reduces greenhouse gas emissions
- adaptation: Increases resilience to climate impacts
- biodiversity: Protects ecosystems and species
- water: Improves water quality or management
- waste: Reduces waste or improves waste management
- pollution: Reduces air, soil, or noise pollution
- circular_economy: Promotes resource efficiency and circularity

SUBCATEGORIES (for GREEN items):
- principal: Primary purpose is environmental (100% weight)
- significant: Has substantial environmental co-benefits (40% weight)
"""

LLM_USER_PROMPT_TEMPLATE = """Classify this budget line item:

Functional Classification: {func1}
Sub-function: {func2}
Economic Classification (Level 1): {econ1}
Economic Classification (Level 2): {econ2}
Administrative Unit: {admin1}
Program/Ministry: {prog1}
Detailed Program: {prog2}

Respond ONLY with valid JSON in this exact format:
{{
  "classification": "GREEN" | "NEUTRAL" | "BROWN",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "environmental_objective": "mitigation" | "adaptation" | "biodiversity" | "water" | "waste" | "pollution" | "circular_economy" | "none",
  "subcategory": "principal" | "significant" | null,
  "rationale": "Brief explanation (max 50 words)"
}}"""

# --- Rio Marker Specialized Prompts ---
RIO_LLM_SYSTEM_PROMPT = """You are an expert in climate finance and the OECD-DAC Rio Marker system.
Your task is to assign Rio Markers (0, 1, or 2) for Climate Change Mitigation and Adaptation to budget line items.

DEFINITIONS:
- Score 0 (Not Targeted): The activity does not target the objective.
- Score 1 (Significant): The objective is explicitly stated but it is not the fundamental driver for the activity.
- Score 2 (Principal): The objective is explicitly stated as fundamental in the design of the activity.

WEIGHTING GUIDANCE:
- Score 2 (Principal) typically corresponds to 100% climate relevance.
- Score 1 (Significant) typically corresponds to 40% climate relevance.

MITIGATION: Reduces GHG emissions (Renewables, Efficiency, Low-carbon transport).
ADAPTATION: Increases resilience (Flood defense, Drought resistance, Early warning)."""

RIO_LLM_USER_PROMPT = """Classify this budget line item for Rio Markers:

Functional Classification: {func1}
Sub-function: {func2}
Economic Classification (Level 1): {econ1}
Program/Ministry: {prog1}
Detailed Program: {prog2}

Respond ONLY with valid JSON in this exact format:
{{
  "rio_mitigation": 0 | 1 | 2,
  "rio_adaptation": 0 | 1 | 2,
  "rationale": "Brief explanation",
  "confidence": "HIGH" | "MEDIUM" | "LOW"
}}"""

# --- EU Taxonomy Specialized Prompts ---
EU_LLM_SYSTEM_PROMPT = """You are an expert in the EU Taxonomy for sustainable activities.
Your task is to screen budget line items against the 6 EU Environmental Objectives. 

LOGIC: Identify the primary objective first, then determine alignment.
1. mitigation: Climate Change Mitigation
2. adaptation: Climate Change Adaptation
3. water: Water and Marine Resources
4. circular_economy: Transition to a Circular Economy
5. pollution: Pollution Prevention and Control (Note: High pollution items may be classified as BROWN)
6. biodiversity: Protection and Restoration of Biodiversity

Identify if the activity aligns with ANY of these objectives, or if it significantly harms them (BROWN)."""

EU_LLM_USER_PROMPT = """Classify this budget line item for EU Taxonomy:

Functional Classification: {func1}
Sub-function: {func2}
Economic Classification (Level 1): {econ1}
Program/Ministry: {prog1}
Detailed Program: {prog2}

Respond ONLY with valid JSON in this exact format:
{{
  "objectives": {{
    "mitigation": true | false,
    "adaptation": true | false,
    "water": true | false,
    "circular_economy": true | false,
    "pollution": true | false,
    "biodiversity": true | false
  }},
  "primary_objective": "mitigation" | "adaptation" | "water" | "circular_economy" | "pollution" | "biodiversity" | "none",
  "rationale": "Brief explanation",
  "confidence": "HIGH" | "MEDIUM" | "LOW"
}}"""


class LLMTagger:
    """
    LLM-based tagger for ambiguous budget lines.
    Supports Claude (Anthropic) and GPT-4 (OpenAI).
    """

    def __init__(
        self,
        api_key: str,
        provider: str = "anthropic",
        model: str = None
    ):
        self.api_key = api_key
        self.provider = provider.lower()
        self.model = model or self._default_model()
        self.client = self._init_client()

    def _default_model(self) -> str:
        """Get default model for provider"""
        if self.provider == "anthropic":
            return "claude-3-5-sonnet-20240620"
        elif self.provider == "openai":
            return "gpt-4o"
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _init_client(self):
        """Initialize API client"""
        if self.provider == "anthropic":
            try:
                import anthropic
                return anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("Please install anthropic: pip install anthropic")
        elif self.provider == "openai":
            try:
                import openai
                return openai.OpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("Please install openai: pip install openai")
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _call_anthropic(self, prompt: str) -> str:
        """Call Anthropic Claude API"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            temperature=0,
            system=LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI GPT API"""
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=500,
            temperature=0,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse JSON response from LLM"""
        # Try to extract JSON from response
        try:
            # Handle potential markdown code blocks
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]

            return json.loads(response.strip())
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM response: {response[:100]}...")
            return {
                "classification": "NEUTRAL",
                "confidence": "LOW",
                "environmental_objective": "none",
                "subcategory": None,
                "rationale": "Failed to parse LLM response"
            }

    def tag_row(self, row: pd.Series, column_mapping: Dict[str, str]) -> TaggingResult:
        """
        Tag a single row using LLM.

        Args:
            row: DataFrame row
            column_mapping: Mapping of standard names to actual column names

        Returns:
            TaggingResult with classification
        """
        # Build prompt with row data
        prompt = LLM_USER_PROMPT_TEMPLATE.format(
            func1=row.get(column_mapping.get("func1", ""), "N/A"),
            func2=row.get(column_mapping.get("func2", ""), "N/A"),
            econ1=row.get(column_mapping.get("econ1", ""), "N/A"),
            econ2=row.get(column_mapping.get("econ2", ""), "N/A"),
            admin1=row.get(column_mapping.get("admin1", ""), "N/A"),
            prog1=row.get(column_mapping.get("prog1", ""), "N/A"),
            prog2=row.get(column_mapping.get("prog2", ""), "N/A"),
        )

        # Call appropriate API
        if self.provider == "anthropic":
            response = self._call_anthropic(prompt)
        else:
            response = self._call_openai(prompt)

        # Parse response
        parsed = self._parse_response(response)

        # Convert to TaggingResult
        return TaggingResult(
            climate_tag=ClimateTag[parsed.get("classification", "NEUTRAL")],
            confidence=Confidence[parsed.get("confidence", "MEDIUM")],
            method="LLM",
            environmental_objective=EnvironmentalObjective(
                parsed.get("environmental_objective", "none")
            ),
            subcategory=parsed.get("subcategory"),
            rationale=parsed.get("rationale", "")
        )

    def tag_rio_row(self, row: pd.Series, column_mapping: Dict[str, str]) -> Dict[str, Any]:
        """Specialized LLM tagging for Rio Markers."""
        prompt = RIO_LLM_USER_PROMPT.format(
            func1=row.get(column_mapping.get("func1", ""), "N/A"),
            func2=row.get(column_mapping.get("func2", ""), "N/A"),
            econ1=row.get(column_mapping.get("econ1", ""), "N/A"),
            prog1=row.get(column_mapping.get("prog1", ""), "N/A"),
            prog2=row.get(column_mapping.get("prog2", ""), "N/A"),
        )
        
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0,
                system=RIO_LLM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            ).content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=500,
                temperature=0,
                messages=[
                    {"role": "system", "content": RIO_LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ]
            ).choices[0].message.content
            
        return self._parse_response(response)

    def tag_eu_taxonomy_row(self, row: pd.Series, column_mapping: Dict[str, str]) -> Dict[str, Any]:
        """Specialized LLM tagging for EU Taxonomy."""
        prompt = EU_LLM_USER_PROMPT.format(
            func1=row.get(column_mapping.get("func1", ""), "N/A"),
            func2=row.get(column_mapping.get("func2", ""), "N/A"),
            econ1=row.get(column_mapping.get("econ1", ""), "N/A"),
            prog1=row.get(column_mapping.get("prog1", ""), "N/A"),
            prog2=row.get(column_mapping.get("prog2", ""), "N/A"),
        )
        
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0,
                system=EU_LLM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            ).content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=500,
                temperature=0,
                messages=[
                    {"role": "system", "content": EU_LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ]
            ).choices[0].message.content
            
        return self._parse_response(response)

    def tag_batch(
        self,
        df: pd.DataFrame,
        column_mapping: Dict[str, str],
        batch_size: int = 10
    ) -> List[TaggingResult]:
        """
        Tag multiple rows in batches.

        Args:
            df: DataFrame with rows to tag
            column_mapping: Column name mapping
            batch_size: Number of rows per API call

        Returns:
            List of TaggingResult objects
        """
        results = []
        total = len(df)

        for i, (idx, row) in enumerate(df.iterrows()):
            try:
                result = self.tag_row(row, column_mapping)
                results.append(result)

                if (i + 1) % 10 == 0:
                    logger.info(f"LLM tagged {i + 1}/{total} rows")

            except Exception as e:
                logger.error(f"Error tagging row {idx}: {e}")
                results.append(TaggingResult(
                    climate_tag=ClimateTag.NEUTRAL,
                    confidence=Confidence.LOW,
                    method="LLM_ERROR",
                    rationale=f"Error during LLM tagging: {str(e)[:50]}"
                ))

        return results


# =============================================================================
# EXCEL FILE PROCESSOR
# =============================================================================

class ExcelProcessor:
    """
    Process Excel files with BOOST budget data.
    Finds 'Raw' sheet and identifies relevant columns.
    """

    # Standard column name patterns to look for
    COLUMN_PATTERNS = {
        "admin1": [r"admin\s*1", r"admin.*level.*1", r"administrative", r"ministry"],
        "admin2": [r"admin\s*2", r"admin.*level.*2", r"department"],
        "admin3": [r"admin\s*3", r"admin.*level.*3", r"unit"],
        "econ1": [r"econ\s*1", r"econ.*level.*1", r"economic.*1"],
        "econ2": [r"econ\s*2", r"econ.*level.*2", r"economic.*2"],
        "econ3": [r"econ\s*3", r"econ.*level.*3", r"economic.*3"],
        "econ4": [r"econ\s*4", r"econ.*level.*4", r"economic.*4"],
        "func1": [r"func\s*1", r"func.*level.*1", r"functional.*1", r"cofog.*1"],
        "func2": [r"func\s*2", r"func.*level.*2", r"functional.*2", r"cofog.*2"],
        "func3": [r"func\s*3", r"func.*level.*3", r"functional.*3", r"cofog.*3"],
        "func4": [r"func\s*4", r"func.*level.*4", r"functional.*4", r"cofog.*4"],
        "prog1": [r"prog\s*1", r"prog.*level.*1", r"program.*1", r"programme.*1"],
        "prog2": [r"prog\s*2", r"prog.*level.*2", r"program.*2", r"programme.*2"],
        "amount": [r"amount", r"budget", r"expenditure", r"value"],
        "year": [r"year", r"fiscal.*year", r"period"],
    }

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.excel_file = None
        self.sheet_name = None
        self.df = None
        self.column_mapping = {}

    def load(self) -> pd.DataFrame:
        """Load Excel file and find Raw sheet"""
        logger.info(f"Loading Excel file: {self.file_path}")

        # Load Excel file
        self.excel_file = pd.ExcelFile(self.file_path)

        # Find 'Raw' sheet (case-insensitive)
        self.sheet_name = self._find_raw_sheet()

        logger.info(f"Using sheet: {self.sheet_name}")

        # Read the sheet
        self.df = pd.read_excel(self.excel_file, sheet_name=self.sheet_name)

        logger.info(f"Loaded {len(self.df)} rows, {len(self.df.columns)} columns")

        # Identify column mapping
        self.column_mapping = self._identify_columns()

        logger.info(f"Identified columns: {self.column_mapping}")

        return self.df

    def _find_raw_sheet(self) -> str:
        """Find sheet with 'Raw' in name"""
        sheet_names = self.excel_file.sheet_names

        # Look for exact match first
        for name in sheet_names:
            if name.lower() == "raw":
                return name

        # Look for partial match
        for name in sheet_names:
            if "raw" in name.lower():
                return name

        # Default to first sheet
        logger.warning(f"No 'Raw' sheet found. Available sheets: {sheet_names}")
        logger.warning(f"Using first sheet: {sheet_names[0]}")
        return sheet_names[0]

    def _identify_columns(self) -> Dict[str, str]:
        """Identify which columns match expected patterns"""
        mapping = {}
        columns_lower = {col.lower(): col for col in self.df.columns}

        for standard_name, patterns in self.COLUMN_PATTERNS.items():
            for pattern in patterns:
                for col_lower, col_original in columns_lower.items():
                    if re.search(pattern, col_lower):
                        mapping[standard_name] = col_original
                        break
                if standard_name in mapping:
                    break

        return mapping

    def get_column_mapping(self) -> Dict[str, str]:
        """Get the identified column mapping"""
        return self.column_mapping

    def get_sheet_names(self) -> List[str]:
        """Get all sheet names in the Excel file"""
        if self.excel_file is None:
            self.excel_file = pd.ExcelFile(self.file_path)
        return self.excel_file.sheet_names


# =============================================================================
# MAIN TAGGING PIPELINE
# =============================================================================

class GreenBudgetTagger:
    """
    Main pipeline for green budget tagging.
    Combines COFOG-based and LLM-based approaches.
    """

    def __init__(
        self,
        use_llm: bool = False,
        api_key: str = None,
        llm_provider: str = "anthropic",
        llm_model: str = None
    ):
        self.cofog_tagger = COFOGTagger()
        self.llm_tagger = None
        self.use_llm = use_llm

        if use_llm and api_key:
            self.llm_tagger = LLMTagger(
                api_key=api_key,
                provider=llm_provider,
                model=llm_model
            )

    def process_file(
        self,
        input_path: str,
        output_path: str = None,
        llm_for_ambiguous_only: bool = True
    ) -> pd.DataFrame:
        """
        Process an Excel file and tag all budget lines.

        Args:
            input_path: Path to input Excel file
            output_path: Path to save tagged output (optional)
            llm_for_ambiguous_only: Only use LLM for ambiguous cases

        Returns:
            DataFrame with tagged results
        """
        # Load Excel file
        processor = ExcelProcessor(input_path)
        df = processor.load()
        column_mapping = processor.get_column_mapping()

        # Initialize result columns
        df["climate_tag"] = ""
        df["confidence"] = ""
        df["method"] = ""
        df["environmental_objective"] = ""
        df["subcategory"] = ""
        df["rationale"] = ""

        # Phase 0: COFOG-based tagging
        logger.info("Starting Phase 0: COFOG-based tagging...")
        ambiguous_indices = []

        for idx, row in df.iterrows():
            result = self.cofog_tagger.tag_row(row, column_mapping)

            df.at[idx, "climate_tag"] = result.climate_tag.value
            df.at[idx, "confidence"] = result.confidence.value
            df.at[idx, "method"] = result.method
            df.at[idx, "environmental_objective"] = result.environmental_objective.value
            df.at[idx, "subcategory"] = result.subcategory or ""
            df.at[idx, "rationale"] = result.rationale

            # Track ambiguous cases for LLM
            if result.climate_tag == ClimateTag.AMBIGUOUS or result.confidence == Confidence.LOW:
                ambiguous_indices.append(idx)

        logger.info(f"COFOG tagging complete. {len(ambiguous_indices)} ambiguous cases identified.")

        # Phase 1: LLM-based tagging for ambiguous cases
        if self.use_llm and self.llm_tagger and ambiguous_indices:
            logger.info(f"Starting Phase 1: LLM tagging for {len(ambiguous_indices)} ambiguous cases...")

            for idx in ambiguous_indices:
                try:
                    row = df.loc[idx]
                    result = self.llm_tagger.tag_row(row, column_mapping)

                    df.at[idx, "climate_tag"] = result.climate_tag.value
                    df.at[idx, "confidence"] = result.confidence.value
                    df.at[idx, "method"] = result.method
                    df.at[idx, "environmental_objective"] = result.environmental_objective.value
                    df.at[idx, "subcategory"] = result.subcategory or ""
                    df.at[idx, "rationale"] = result.rationale

                except Exception as e:
                    logger.error(f"LLM tagging failed for row {idx}: {e}")

        # Generate summary statistics
        self._print_summary(df)

        # Save output
        if output_path:
            self._save_output(df, output_path, processor.sheet_name)
            logger.info(f"Tagged data saved to: {output_path}")

        return df

    def _print_summary(self, df: pd.DataFrame):
        """Print summary statistics"""
        total = len(df)

        print("\n" + "="*60)
        print("GREEN BUDGET TAGGING SUMMARY")
        print("="*60)

        # Classification breakdown
        print("\nClassification Breakdown:")
        for tag in ["GREEN", "NEUTRAL", "BROWN", "AMBIGUOUS"]:
            count = (df["climate_tag"] == tag).sum()
            pct = count / total * 100
            print(f"  {tag}: {count:,} ({pct:.1f}%)")

        # Confidence distribution
        print("\nConfidence Distribution:")
        for conf in ["HIGH", "MEDIUM", "LOW"]:
            count = (df["confidence"] == conf).sum()
            pct = count / total * 100
            print(f"  {conf}: {count:,} ({pct:.1f}%)")

        # Method distribution
        print("\nTagging Method:")
        method_counts = df["method"].value_counts()
        for method, count in method_counts.items():
            pct = count / total * 100
            print(f"  {method}: {count:,} ({pct:.1f}%)")

        # Environmental objectives (for GREEN items)
        green_df = df[df["climate_tag"] == "GREEN"]
        if len(green_df) > 0:
            print("\nEnvironmental Objectives (GREEN items):")
            obj_counts = green_df["environmental_objective"].value_counts()
            for obj, count in obj_counts.items():
                if obj and obj != "none":
                    pct = count / len(green_df) * 100
                    print(f"  {obj}: {count:,} ({pct:.1f}%)")

        print("="*60 + "\n")

    def _save_output(self, df: pd.DataFrame, output_path: str, original_sheet: str):
        """Save tagged DataFrame to Excel"""
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Main tagged data
            df.to_excel(writer, sheet_name="Tagged_Data", index=False)

            # Summary statistics sheet
            summary_data = {
                "Metric": [
                    "Total Budget Lines",
                    "GREEN Count", "GREEN %",
                    "NEUTRAL Count", "NEUTRAL %",
                    "BROWN Count", "BROWN %",
                    "HIGH Confidence", "MEDIUM Confidence", "LOW Confidence"
                ],
                "Value": [
                    len(df),
                    (df["climate_tag"] == "GREEN").sum(),
                    f"{(df['climate_tag'] == 'GREEN').sum() / len(df) * 100:.1f}%",
                    (df["climate_tag"] == "NEUTRAL").sum(),
                    f"{(df['climate_tag'] == 'NEUTRAL').sum() / len(df) * 100:.1f}%",
                    (df["climate_tag"] == "BROWN").sum(),
                    f"{(df['climate_tag'] == 'BROWN').sum() / len(df) * 100:.1f}%",
                    (df["confidence"] == "HIGH").sum(),
                    (df["confidence"] == "MEDIUM").sum(),
                    (df["confidence"] == "LOW").sum(),
                ]
            }
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name="Summary", index=False)


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Green Budget Tagging System for World Bank BOOST Data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic COFOG-based tagging only
  python green_budget_tagger.py input.xlsx -o output.xlsx

  # With LLM tagging for ambiguous cases (Anthropic/Claude)
  python green_budget_tagger.py input.xlsx -o output.xlsx --use-llm --api-key YOUR_KEY

  # Using OpenAI instead
  python green_budget_tagger.py input.xlsx -o output.xlsx --use-llm --provider openai --api-key YOUR_KEY
        """
    )

    parser.add_argument(
        "input_file",
        help="Path to input Excel file with BOOST budget data"
    )

    parser.add_argument(
        "-o", "--output",
        help="Path to output Excel file (default: input_tagged.xlsx)",
        default=None
    )

    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use LLM for ambiguous cases (requires API key)"
    )

    parser.add_argument(
        "--api-key",
        help="API key for LLM provider (or set ANTHROPIC_API_KEY/OPENAI_API_KEY env var)"
    )

    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="LLM provider to use (default: anthropic)"
    )

    parser.add_argument(
        "--model",
        help="Specific model to use (default: provider's recommended model)"
    )

    parser.add_argument(
        "--list-sheets",
        action="store_true",
        help="List all sheets in the Excel file and exit"
    )

    args = parser.parse_args()

    # Validate input file
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    # List sheets mode
    if args.list_sheets:
        processor = ExcelProcessor(str(input_path))
        sheets = processor.get_sheet_names()
        print(f"Sheets in {input_path.name}:")
        for i, sheet in enumerate(sheets, 1):
            print(f"  {i}. {sheet}")
        return 0

    # Determine output path
    output_path = args.output
    if output_path is None:
        output_path = input_path.stem + "_tagged.xlsx"

    # Get API key from args or environment
    api_key = args.api_key
    if args.use_llm and not api_key:
        import os
        if args.provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        else:
            api_key = os.environ.get("OPENAI_API_KEY")

        if not api_key:
            print(f"Error: --use-llm requires API key via --api-key or {args.provider.upper()}_API_KEY env var")
            return 1

    # Create tagger and process
    tagger = GreenBudgetTagger(
        use_llm=args.use_llm,
        api_key=api_key,
        llm_provider=args.provider,
        llm_model=args.model
    )

    try:
        tagger.process_file(str(input_path), output_path)
        print(f"\nTagging complete! Output saved to: {output_path}")
        return 0
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
