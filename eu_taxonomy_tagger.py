"""
EU Taxonomy Tagger

Tag budget lines using the 6 EU Environmental Objectives.
Provides classification into Mitigation, Adaptation, Water, Circular Economy, Pollution, and Biodiversity.
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
from shared.eu_taxonomy_keywords import (
    EU_OBJECTIVES_KEYWORDS,
    BROWN_KEYWORDS,
    get_eu_objective_label,
)
from shared.rio_keywords import EXCLUSION_TERMS

logger = logging.getLogger(__name__)

@dataclass
class EUTaxonomyResult:
    """Extended TaggingResult with EU Taxonomy specific fields."""
    tagging_result: TaggingResult
    eu_objectives: Dict[str, bool]  # Map of objective key to boolean presence
    primary_objective: str  # The most relevant objective

class EUTaxonomyTagger:
    """
    Tag budget lines using EU Taxonomy methodology.
    Focuses on the 6 environmental objectives.
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
        """Initialize with keyword dictionaries."""
        self.objective_keywords = EU_OBJECTIVES_KEYWORDS
        self.brown_keywords = BROWN_KEYWORDS
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
        """Check if text contains any keyword (case-insensitive, word boundary)."""
        text_lower = text.lower()
        for keyword in keywords:
            pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
            if re.search(pattern, text_lower):
                return True
        return False

    def get_matched_keyword(self, text: str, keywords: List[str]) -> Optional[str]:
        """Find which keyword matched in the text."""
        text_lower = text.lower()
        for keyword in keywords:
            pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
            if re.search(pattern, text_lower):
                return keyword
        return None

    def tag_row(self, row: pd.Series, column_mapping: Dict) -> EUTaxonomyResult:
        """Tag a single row using EU Taxonomy logic."""
        # Extract text fields
        project_title = self._get_text_field(row, column_mapping, ["project_title", "prog1"])
        project_desc = self._get_text_field(row, column_mapping, ["project_description", "prog2"])
        sector = self._get_text_field(row, column_mapping, ["sector", "admin1", "department"])

        combined_text = " ".join([project_title, project_desc, sector]).lower()

        # Step 0: Check for predetermined tags based on COFOG code
        func1 = row.get(column_mapping.get("func1", "func1"), "")
        predetermined = self.cofog_tagger.get_predetermined_tag(func1, method="eu")
        if predetermined:
            primary = predetermined["primary_objective"]
            # Map to enum
            env_obj_enum = EnvironmentalObjective.NONE
            if primary in [e.value for e in EnvironmentalObjective]:
                env_obj_enum = EnvironmentalObjective(primary)

            return EUTaxonomyResult(
                tagging_result=TaggingResult(
                    climate_tag=ClimateTag[predetermined["climate_tag"]],
                    confidence=Confidence.HIGH,
                    method="EU_PREDETERMINED",
                    environmental_objective=env_obj_enum,
                    subcategory="taxonomy_aligned",
                    rationale=predetermined["rationale"],
                ),
                eu_objectives={k: (k == primary) for k in self.objective_keywords.keys()},
                primary_objective=primary
            )

        # Step 1: Check exclusions
        excluded_keyword = self.get_matched_keyword(combined_text, self.exclusion_terms)
        if excluded_keyword:
            return EUTaxonomyResult(
                tagging_result=TaggingResult(
                    climate_tag=ClimateTag.NEUTRAL,
                    confidence=Confidence.HIGH,
                    method="EU_EXCLUSION",
                    environmental_objective=EnvironmentalObjective.NONE,
                    subcategory=None,
                    rationale=f"Exclusion term found: '{excluded_keyword}'",
                ),
                eu_objectives={k: False for k in self.objective_keywords.keys()},
                primary_objective="none"
            )

        # Step 2: Check for BROWN keywords (negative screening)
        brown_match = self.get_matched_keyword(combined_text, self.brown_keywords)
        if brown_match:
            return EUTaxonomyResult(
                tagging_result=TaggingResult(
                    climate_tag=ClimateTag.BROWN,
                    confidence=Confidence.MEDIUM,
                    method="EU_BROWN",
                    environmental_objective=EnvironmentalObjective.NONE,
                    subcategory=None,
                    rationale=f"Brown keyword found: '{brown_match}'",
                ),
                eu_objectives={k: False for k in self.objective_keywords.keys()},
                primary_objective="none"
            )

        # Step 3: Check each of the 6 objectives
        obj_matches = {}
        matched_keywords = {}
        for obj_key, keywords in self.objective_keywords.items():
            match = self.get_matched_keyword(combined_text, keywords)
            if match:
                obj_matches[obj_key] = True
                matched_keywords[obj_key] = match
            else:
                obj_matches[obj_key] = False

        # Step 3: Determine primary objective and final tag
        active_objectives = [k for k, v in obj_matches.items() if v]
        
        if active_objectives:
            primary = active_objectives[0]
            # Simple heuristic: first matched objective is primary
            climate_tag = ClimateTag.GREEN
            confidence = Confidence.HIGH if len(active_objectives) == 1 else Confidence.MEDIUM
            
            # Map back to EnvironmentalObjective enum if possible
            env_obj_enum = EnvironmentalObjective.NONE
            if primary == "mitigation":
                env_obj_enum = EnvironmentalObjective.MITIGATION
            elif primary == "adaptation":
                env_obj_enum = EnvironmentalObjective.ADAPTATION
            elif primary == "biodiversity":
                env_obj_enum = EnvironmentalObjective.BIODIVERSITY
            elif primary == "water":
                env_obj_enum = EnvironmentalObjective.WATER
            elif primary == "circular_economy":
                env_obj_enum = EnvironmentalObjective.CIRCULAR_ECONOMY
            elif primary == "pollution":
                env_obj_enum = EnvironmentalObjective.POLLUTION

            rationale = f"Aligned with EU Objective: {get_eu_objective_label(primary)} (Keyword: '{matched_keywords[primary]}')"
            if len(active_objectives) > 1:
                others = [get_eu_objective_label(k) for k in active_objectives[1:]]
                rationale += f". Also matches: {', '.join(others)}"

            return EUTaxonomyResult(
                tagging_result=TaggingResult(
                    climate_tag=climate_tag,
                    confidence=confidence,
                    method="EU_TAXONOMY",
                    environmental_objective=env_obj_enum,
                    subcategory="taxonomy_aligned",
                    rationale=rationale,
                ),
                eu_objectives=obj_matches,
                primary_objective=primary
            )
        # Step 3.5: Hybrid Logic - Use LLM if enabled and conditions met
        use_llm_now = self.use_llm and self.llm_tagger
        if use_llm_now:
            # In "Deep" mode, we always use LLM unless excluded.
            # In "Balanced" mode, we only use LLM if no keyword match was found.
            should_call_llm = not self.llm_for_ambiguous_only or not active_objectives
            
            if should_call_llm and not excluded_keyword:
                llm_res = self.llm_tagger.tag_eu_taxonomy_row(row, column_mapping)
                
                # Check if any objective was found
                llm_objs = llm_res.get("objectives", {})
                if any(llm_objs.values()):
                    primary = llm_res.get("primary_objective", "none")
                    
                    # Map to enum
                    env_obj_enum = EnvironmentalObjective.NONE
                    if primary in [e.value for e in EnvironmentalObjective]:
                        env_obj_enum = EnvironmentalObjective(primary)
                    
                    return EUTaxonomyResult(
                        tagging_result=TaggingResult(
                            climate_tag=ClimateTag.GREEN,
                            confidence=Confidence[llm_res.get("confidence", "MEDIUM")],
                            method="EU_LLM",
                            environmental_objective=env_obj_enum,
                            subcategory="taxonomy_aligned",
                            rationale=llm_res.get("rationale", ""),
                        ),
                        eu_objectives=llm_objs,
                        primary_objective=primary
                    )

        return EUTaxonomyResult(
            tagging_result=TaggingResult(
                climate_tag=ClimateTag.NEUTRAL,
                confidence=Confidence.HIGH,
                method="EU_TAXONOMY_NONE",
                environmental_objective=EnvironmentalObjective.NONE,
                subcategory=None,
                rationale="No EU Taxonomy environmental objectives detected",
            ),
            eu_objectives=obj_matches,
            primary_objective="none"
        )

    def _get_text_field(self, row: pd.Series, column_mapping: Dict, possible_names: List[str]) -> str:
        """Safely extract text field from row."""
        for std_name in possible_names:
            if std_name in row.index:
                return str(row[std_name]) if pd.notna(row[std_name]) else ""
            if std_name in column_mapping:
                actual_name = column_mapping[std_name]
                if actual_name in row.index:
                    return str(row[actual_name]) if pd.notna(row[actual_name]) else ""
        return ""

    def tag_batch(self, df: pd.DataFrame, column_mapping: Dict) -> List[EUTaxonomyResult]:
        """Tag multiple rows in a dataframe."""
        logger.info(f"Starting EU Taxonomy tagging for {len(df)} rows...")
        results = []
        for i, (idx, row) in enumerate(df.iterrows()):
            try:
                result = self.tag_row(row, column_mapping)
                results.append(result)
                if (i + 1) % 100 == 0:
                    logger.info(f"EU Taxonomied {i + 1}/{len(df)} rows")
            except Exception as e:
                logger.error(f"Error tagging row {idx}: {e}")
                results.append(EUTaxonomyResult(
                    tagging_result=TaggingResult(
                        climate_tag=ClimateTag.NEUTRAL,
                        confidence=Confidence.LOW,
                        method="EU_ERROR",
                        environmental_objective=EnvironmentalObjective.NONE,
                        subcategory=None,
                        rationale=f"Error during EU Taxonomy tagging: {str(e)[:50]}",
                    ),
                    eu_objectives={k: False for k in self.objective_keywords.keys()},
                    primary_objective="error"
                ))
        return results
