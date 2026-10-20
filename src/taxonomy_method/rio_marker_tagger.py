"""
Rio Convention Marker Tagger

Tag budget lines using Rio Convention/Paris Agreement methodology.
Provides Rio Markers (0, 1, 2) for tracking climate relevance.
"""

import re
import pandas as pd
from typing import Dict, List, Optional
import logging
from dataclasses import dataclass

# Import from existing tagger
from green_budget_tagger import (
    EnvironmentalObjective,
    TaggingResult,
    LLMTagger,
    COFOGTagger,
    ClimateTag,
    Confidence,
)
from shared.rio_keywords import (
    MITIGATION_KEYWORDS,
    ADAPTATION_KEYWORDS,
    EXPLICIT_CLIMATE_TERMS,
    EXCLUSION_TERMS,
    RIO_MARKER_NOT_TARGETED,
    RIO_MARKER_SIGNIFICANT,
    RIO_MARKER_PRINCIPAL,
    get_rio_marker_label,
)

logger = logging.getLogger(__name__)


@dataclass
class RioMarkerResult:
    """Extended TaggingResult with Rio Marker specific fields."""

    tagging_result: TaggingResult
    rio_marker: int  # 0, 1, or 2
    rio_mitigation: int  # 0, 1, or 2
    rio_adaptation: int  # 0, 1, or 2


class RioMarkerTagger:
    """
    Tag budget lines using Rio Convention methodology.

    Rio Marker System:
    - 0: Not targeted (no clear climate relevance)
    - 1: Significant objective (climate is one of several objectives)
    - 2: Principal objective (climate is the main purpose)
    """

    def __init__(
        self,
        use_llm: bool = False,
        api_key: str = None,
        llm_provider: str = "anthropic",
        llm_model: str = None,
        llm_for_ambiguous_only: bool = True,
        mapping_path: Optional[str] = None
    ):
        """Initialize the Rio Marker tagger with keyword dictionaries."""
        self.mitigation_keywords = MITIGATION_KEYWORDS
        self.adaptation_keywords = ADAPTATION_KEYWORDS
        self.explicit_climate_terms = EXPLICIT_CLIMATE_TERMS
        self.exclusion_terms = EXCLUSION_TERMS
        
        self.cofog_tagger = COFOGTagger(mapping_path)
        self.use_llm = use_llm
        self.llm_for_ambiguous_only = llm_for_ambiguous_only
        self.llm_tagger = None
        if use_llm and api_key:
            self.llm_tagger = LLMTagger(
                api_key=api_key,
                provider=llm_provider,
                model=llm_model
            )

    def contains_any(self, text: str, keywords: List[str]) -> bool:
        """
        Check if text contains any keyword (case-insensitive, word boundary).

        Args:
            text: Text to search
            keywords: List of keywords to look for

        Returns:
            True if any keyword found (with word boundary)
        """
        text_lower = text.lower()
        for keyword in keywords:
            pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
            if re.search(pattern, text_lower):
                return True
        return False

    def get_matched_keyword(self, text: str, keywords: List[str]) -> Optional[str]:
        """
        Find which keyword matched in the text.

        Args:
            text: Text to search
            keywords: List of keywords to look for

        Returns:
            Matched keyword string, or None if no match
        """
        text_lower = text.lower()
        for keyword in keywords:
            pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
            if re.search(pattern, text_lower):
                return keyword
        return None

    def tag_row(self, row: pd.Series, column_mapping: Dict) -> RioMarkerResult:
        """
        Tag a single row using Rio Marker logic.

        Args:
            row: Pandas Series representing one budget line
            column_mapping: Dictionary mapping standard names to actual column names

        Returns:
            RioMarkerResult with:
                - tagging_result: Standard TaggingResult (climate_tag, confidence, etc.)
                - rio_marker: Combined Rio Marker (0, 1, 2)
                - rio_mitigation: Mitigation-specific marker (0, 1, 2)
                - rio_adaptation: Adaptation-specific marker (0, 1, 2)
        """
        # Extract text fields (try multiple column name variations)
        project_title = self._get_text_field(
            row, column_mapping, ["project_title", "prog1"]
        )
        project_desc = self._get_text_field(
            row, column_mapping, ["project_description", "prog2"]
        )
        sector = self._get_text_field(
            row, column_mapping, ["sector", "admin1", "department"]
        )

        # Combine all text fields for analysis
        combined_text = " ".join([project_title, project_desc, sector]).lower()

        # Step 0: Check for predetermined tags based on COFOG code
        func1 = row.get(column_mapping.get("func1", "func1"), "")
        predetermined = self.cofog_tagger.get_predetermined_tag(func1, method="rio")
        if predetermined:
            return RioMarkerResult(
                tagging_result=TaggingResult(
                    climate_tag=ClimateTag[predetermined["climate_tag"]],
                    confidence=Confidence.HIGH,
                    method="RIO_PREDETERMINED",
                    environmental_objective=EnvironmentalObjective.NONE,
                    subcategory="principal" if predetermined.get("rio_mitigation", 0) == 2 else "significant",
                    rationale=predetermined["rationale"],
                ),
                rio_marker=max(predetermined.get("rio_mitigation", 0), predetermined.get("rio_adaptation", 0)),
                rio_mitigation=predetermined.get("rio_mitigation", 0),
                rio_adaptation=predetermined.get("rio_adaptation", 0),
            )

        # Step 1: Check exclusions first (conservative tagging)
        excluded_keyword = self.get_matched_keyword(combined_text, self.exclusion_terms)
        if excluded_keyword:
            return RioMarkerResult(
                tagging_result=TaggingResult(
                    climate_tag=ClimateTag.NEUTRAL,
                    confidence=Confidence.HIGH,
                    method="RIO_EXCLUSION",
                    environmental_objective=EnvironmentalObjective.NONE,
                    subcategory=None,
                    rationale=f"Exclusion term found: '{excluded_keyword}'",
                ),
                rio_marker=RIO_MARKER_NOT_TARGETED,
                rio_mitigation=RIO_MARKER_NOT_TARGETED,
                rio_adaptation=RIO_MARKER_NOT_TARGETED,
            )

        # Step 2: Check for climate keywords
        has_mitigation = self.contains_any(combined_text, self.mitigation_keywords)
        has_adaptation = self.contains_any(combined_text, self.adaptation_keywords)
        has_explicit = self.contains_any(combined_text, self.explicit_climate_terms)

        mitigation_keyword = self.get_matched_keyword(
            combined_text, self.mitigation_keywords
        )
        adaptation_keyword = self.get_matched_keyword(
            combined_text, self.adaptation_keywords
        )

        # Step 3: Determine Rio Marker values

        # RIO_MITIGATION marker (0, 1, 2)
        if has_explicit and has_mitigation:
            rio_mitigation = RIO_MARKER_PRINCIPAL
        elif has_mitigation:
            rio_mitigation = RIO_MARKER_SIGNIFICANT
        else:
            rio_mitigation = RIO_MARKER_NOT_TARGETED

        # RIO_ADAPTATION marker (0, 1, 2)
        if has_explicit and has_adaptation:
            rio_adaptation = RIO_MARKER_PRINCIPAL
        elif has_adaptation:
            rio_adaptation = RIO_MARKER_SIGNIFICANT
        else:
            rio_adaptation = RIO_MARKER_NOT_TARGETED

        # Combined RIO_MARKER (0, 1, 2) - principal if either is principal
        if (
            rio_mitigation == RIO_MARKER_PRINCIPAL
            or rio_adaptation == RIO_MARKER_PRINCIPAL
        ):
            rio_marker = RIO_MARKER_PRINCIPAL
        elif (
            rio_mitigation == RIO_MARKER_SIGNIFICANT
            or rio_adaptation == RIO_MARKER_SIGNIFICANT
        ):
            rio_marker = RIO_MARKER_SIGNIFICANT
        else:
            rio_marker = RIO_MARKER_NOT_TARGETED

        # Step 3.5: Hybrid Logic - Use LLM if enabled and conditions met
        use_llm_now = self.use_llm and self.llm_tagger
        if use_llm_now:
            # In "Deep" mode, we always use LLM unless excluded.
            # In "Balanced" mode, we only use LLM if no keyword match was found.
            should_call_llm = not self.llm_for_ambiguous_only or rio_marker == RIO_MARKER_NOT_TARGETED
            
            if should_call_llm and not excluded_keyword:
                llm_res = self.llm_tagger.tag_rio_row(row, column_mapping)
                if llm_res.get("rio_mitigation", 0) > 0 or llm_res.get("rio_adaptation", 0) > 0:
                    rio_mitigation = llm_res.get("rio_mitigation", 0)
                    rio_adaptation = llm_res.get("rio_adaptation", 0)
                    rio_marker = max(rio_mitigation, rio_adaptation)
                    
                    # Convert to TaggingResult format
                    climate_tag = ClimateTag.GREEN
                    confidence = Confidence[llm_res.get("confidence", "MEDIUM")]
                    rationale = llm_res.get("rationale", "")
                    
                    # Determine objective
                    if rio_mitigation > 0 and rio_adaptation > 0:
                        environmental_objective = EnvironmentalObjective.NONE
                    elif rio_mitigation > 0:
                        environmental_objective = EnvironmentalObjective.MITIGATION
                    else:
                        environmental_objective = EnvironmentalObjective.ADAPTATION
                        
                    tagging_result = TaggingResult(
                        climate_tag=climate_tag,
                        confidence=confidence,
                        method="RIO_LLM",
                        environmental_objective=environmental_objective,
                        subcategory="principal" if rio_marker == 2 else "significant",
                        rationale=rationale,
                    )
                    
                    return RioMarkerResult(
                        tagging_result=tagging_result,
                        rio_marker=rio_marker,
                        rio_mitigation=rio_mitigation,
                        rio_adaptation=rio_adaptation,
                    )

        # Step 4: Determine climate_tag and environmental_objective
        if rio_marker == RIO_MARKER_PRINCIPAL:
            climate_tag = ClimateTag.GREEN
            confidence = Confidence.HIGH
            if has_mitigation and has_adaptation:
                environmental_objective = EnvironmentalObjective.NONE
                subcategory = "principal"
                rationale = (
                    f"Principal climate objective (both mitigation and adaptation)"
                )
            elif has_mitigation:
                environmental_objective = EnvironmentalObjective.MITIGATION
                subcategory = "principal"
                rationale = (
                    f"Principal mitigation objective (explicit: '{mitigation_keyword}')"
                )
            elif has_adaptation:
                environmental_objective = EnvironmentalObjective.ADAPTATION
                subcategory = "principal"
                rationale = (
                    f"Principal adaptation objective (explicit: '{adaptation_keyword}')"
                )
            else:
                environmental_objective = EnvironmentalObjective.NONE
                subcategory = "principal"
                rationale = "Principal climate objective (explicit climate terms)"

        elif rio_marker == RIO_MARKER_SIGNIFICANT:
            climate_tag = ClimateTag.GREEN
            confidence = Confidence.MEDIUM
            if has_mitigation and has_adaptation:
                environmental_objective = EnvironmentalObjective.NONE
                subcategory = "significant"
                rationale = (
                    "Significant climate objective (both mitigation and adaptation)"
                )
            elif has_mitigation:
                environmental_objective = EnvironmentalObjective.MITIGATION
                subcategory = "significant"
                rationale = f"Significant mitigation objective (keyword: '{mitigation_keyword}')"
            elif has_adaptation:
                environmental_objective = EnvironmentalObjective.ADAPTATION
                subcategory = "significant"
                rationale = f"Significant adaptation objective (keyword: '{adaptation_keyword}')"
            else:
                environmental_objective = EnvironmentalObjective.NONE
                subcategory = "significant"
                rationale = (
                    "Significant climate objective (mitigation/adaptation keywords)"
                )

        else:
            climate_tag = ClimateTag.NEUTRAL
            confidence = Confidence.HIGH
            environmental_objective = EnvironmentalObjective.NONE
            subcategory = None
            rationale = "No climate relevance detected"

        # Step 5: Create TaggingResult and RioMarkerResult
        tagging_result = TaggingResult(
            climate_tag=climate_tag,
            confidence=confidence,
            method="RIO_MARKER",
            environmental_objective=environmental_objective,
            subcategory=subcategory,
            rationale=rationale,
        )

        return RioMarkerResult(
            tagging_result=tagging_result,
            rio_marker=rio_marker,
            rio_mitigation=rio_mitigation,
            rio_adaptation=rio_adaptation,
        )

    def _get_text_field(
        self, row: pd.Series, column_mapping: Dict, possible_names: List[str]
    ) -> str:
        """
        Safely extract text field from row, trying multiple possible column names.

        Args:
            row: Pandas Series
            column_mapping: Column name mapping
            possible_names: List of possible standard column names

        Returns:
            String value, or empty string if not found
        """
        for std_name in possible_names:
            # Try exact match first
            if std_name in row.index:
                return str(row[std_name]) if pd.notna(row[std_name]) else ""

            # Try mapped name
            if std_name in column_mapping:
                actual_name = column_mapping[std_name]
                if actual_name in row.index:
                    return str(row[actual_name]) if pd.notna(row[actual_name]) else ""

        return ""

    def tag_batch(
        self, df: pd.DataFrame, column_mapping: Dict
    ) -> List[RioMarkerResult]:
        """
        Tag multiple rows in a dataframe.

        Args:
            df: DataFrame with rows to tag
            column_mapping: Column name mapping

        Returns:
            List of RioMarkerResult objects
        """
        logger.info(f"Starting Rio Marker tagging for {len(df)} rows...")

        results = []
        total = len(df)

        for i, (idx, row) in enumerate(df.iterrows()):
            try:
                result = self.tag_row(row, column_mapping)
                results.append(result)

                if (i + 1) % 100 == 0:
                    logger.info(f"Rio Markered {i + 1}/{total} rows")

            except Exception as e:
                logger.error(f"Error Rio Marker tagging row {idx}: {e}")
                # Return default result on error
                results.append(
                    RioMarkerResult(
                        tagging_result=TaggingResult(
                            climate_tag=ClimateTag.NEUTRAL,
                            confidence=Confidence.LOW,
                            method="RIO_ERROR",
                            environmental_objective=EnvironmentalObjective.NONE,
                            subcategory=None,
                            rationale=f"Error during Rio Marker tagging: {str(e)[:50]}",
                        ),
                        rio_marker=RIO_MARKER_NOT_TARGETED,
                        rio_mitigation=RIO_MARKER_NOT_TARGETED,
                        rio_adaptation=RIO_MARKER_NOT_TARGETED,
                    )
                )

        logger.info(
            f"Rio Marker tagging complete. Total: {total}, Successful: {len(results)}"
        )
        return results
