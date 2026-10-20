"""
Green Budget Tagging System - OPTIMIZED VERSION
===============================================
An optimized version that uses deduplication to dramatically reduce LLM calls.

Key Improvement: Instead of tagging each row individually, we:
1. Identify unique combinations of classification fields (func1-4, econ1-4, prog1-2, admin1-2)
2. Tag only the unique combinations (much faster)
3. Map results back to all rows (instant)

Result: For typical BOOST data with 15,000 rows but only 1,000 unique combinations,
this reduces LLM calls from 15,000 to ~1,000 (90% reduction!)

Usage:
    python green_budget_tagger_optimized.py input.xlsx --output tagged_output.xlsx
    python green_budget_tagger_optimized.py input.xlsx --output tagged_output.xlsx --use-llm --api-key YOUR_KEY
"""

import pandas as pd
import re
import json
import argparse
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any, Set
from dataclasses import dataclass
from enum import Enum

# Import all the tagging logic from original (unchanged)
from green_budget_tagger import (
    ClimateTag, Confidence, EnvironmentalObjective, TaggingResult,
    FUNC1_DEFAULT_TAGS, ECON_SIGNALS, GREEN_KEYWORDS, BROWN_KEYWORDS,
    EXCLUSION_CONTEXTS, COFOGTagger, LLMTagger, ExcelProcessor,
    LLM_SYSTEM_PROMPT, LLM_USER_PROMPT_TEMPLATE
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeduplicationHelper:
    """
    Handles deduplication of budget lines for efficient tagging.

    Instead of tagging each row, identifies unique combinations of
    classification fields and only tags unique items.
    """

    def __init__(self, column_mapping: Dict[str, str]):
        """
        Initialize deduplication helper.

        Args:
            column_mapping: Mapping of standard names to actual column names
        """
        self.column_mapping = column_mapping
        # Fields used for deduplication
        self.dedup_fields = [
            'func1', 'func2', 'func3', 'func4',
            'econ1', 'econ2', 'econ3', 'econ4',
            'prog1', 'prog2',
            'admin1', 'admin2'
        ]

    def get_dedup_key(self, row: pd.Series) -> Tuple:
        """
        Create a deduplication key from a row.

        Args:
            row: DataFrame row

        Returns:
            Tuple of values that uniquely identifies this classification
        """
        key_values = []
        for field in self.dedup_fields:
            col_name = self.column_mapping.get(field, "")
            if col_name and col_name in row.index:
                val = row[col_name]
                # Convert to string and handle NaN
                val_str = str(val) if pd.notna(val) else ""
                key_values.append(val_str)
            else:
                key_values.append("")

        return tuple(key_values)

    def deduplicate(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[int, List[int]], List[int]]:
        """
        Deduplicate DataFrame by classification fields.

        Args:
            df: Input DataFrame with all rows

        Returns:
            Tuple of:
            - dedup_df: DataFrame with only unique combinations
            - mapping: Dict mapping dedup_index -> list of original indices
            - dedup_indices: List of indices in deduplicated DataFrame
        """
        dedup_map = {}  # key -> (dedup_index, list of original indices)
        dedup_indices = []
        mapping = {}  # dedup_index -> original indices
        dedup_index = 0

        for orig_idx, row in df.iterrows():
            key = self.get_dedup_key(row)

            if key not in dedup_map:
                # First time seeing this combination
                dedup_map[key] = (dedup_index, [orig_idx])
                dedup_indices.append(orig_idx)
                mapping[dedup_index] = [orig_idx]
                dedup_index += 1
            else:
                # Seen before - add to existing group
                _, orig_indices = dedup_map[key]
                orig_indices.append(orig_idx)
                mapping[dedup_map[key][0]].append(orig_idx)

        # Create deduplicated DataFrame
        dedup_df = df.loc[dedup_indices].reset_index(drop=True)

        return dedup_df, mapping, dedup_indices

    def expand_results(self, dedup_results: List[TaggingResult],
                       mapping: Dict[int, List[int]],
                       original_length: int) -> List[TaggingResult]:
        """
        Expand deduplicated results back to original rows.

        Args:
            dedup_results: List of TaggingResult for deduplicated rows
            mapping: Mapping of dedup index to original indices
            original_length: Total number of original rows

        Returns:
            List of TaggingResult with same length as original DataFrame
        """
        expanded = [None] * original_length

        for dedup_idx, result in enumerate(dedup_results):
            original_indices = mapping[dedup_idx]
            for orig_idx in original_indices:
                expanded[orig_idx] = result

        return expanded


# =============================================================================
# OPTIMIZED TAGGING PIPELINE
# =============================================================================

class GreenBudgetTaggerOptimized:
    """
    Optimized pipeline for green budget tagging using deduplication.

    Key differences from original:
    - Deduplicates rows before tagging
    - Only tags unique combinations
    - Maps results back to all rows
    - Dramatically reduces LLM API calls
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
        llm_for_ambiguous_only: bool = True,
        column_mapping_override: Optional[Dict[str, str]] = None
    ) -> pd.DataFrame:
        """
        Process an Excel file and tag all budget lines with deduplication.

        Args:
            input_path: Path to input Excel file
            output_path: Path to save tagged output (optional)
            llm_for_ambiguous_only: Only use LLM for ambiguous cases
            column_mapping_override: Manual column mapping to use instead of auto-detection

        Returns:
            DataFrame with tagged results
        """
        # Load Excel file
        processor = ExcelProcessor(input_path)
        df = processor.load()
        
        if column_mapping_override:
            column_mapping = column_mapping_override
            logger.info("Using manual column mapping override")
        else:
            column_mapping = processor.get_column_mapping()

        logger.info(f"Loaded {len(df):,} total rows")

        # Initialize deduplication helper
        dedup_helper = DeduplicationHelper(column_mapping)

        # Deduplicate
        logger.info("Deduplicating rows by classification fields...")
        dedup_df, mapping, dedup_indices = dedup_helper.deduplicate(df)

        reduction = len(df) - len(dedup_df)
        reduction_pct = 100 * reduction / len(df) if len(df) > 0 else 0
        logger.info(f"Deduplication complete:")
        logger.info(f"  Original rows: {len(df):,}")
        logger.info(f"  Unique combinations: {len(dedup_df):,}")
        logger.info(f"  Duplicate rows skipped: {reduction:,} ({reduction_pct:.1f}%)")
        logger.info(f"  LLM calls reduced by ~{reduction_pct:.0f}%!")

        # Initialize result columns
        df["climate_tag"] = ""
        df["confidence"] = ""
        df["method"] = ""
        df["environmental_objective"] = ""
        df["subcategory"] = ""
        df["rationale"] = ""

        # Phase 0: COFOG-based tagging on deduplicated data
        logger.info("Starting Phase 0: COFOG-based tagging on unique combinations...")
        ambiguous_dedup_indices = []
        cofog_results = []

        for idx, row in dedup_df.iterrows():
            result = self.cofog_tagger.tag_row(row, column_mapping)
            cofog_results.append(result)

            # Track ambiguous cases for LLM
            if result.climate_tag == ClimateTag.AMBIGUOUS or result.confidence == Confidence.LOW:
                ambiguous_dedup_indices.append(idx)

            if (idx + 1) % 100 == 0:
                logger.info(f"COFOG tagged {idx + 1}/{len(dedup_df)} unique combinations")

        logger.info(f"COFOG tagging complete. {len(ambiguous_dedup_indices)} ambiguous cases identified.")

        # Phase 1: LLM-based tagging for ambiguous cases (on deduplicated data)
        if self.use_llm and self.llm_tagger and ambiguous_dedup_indices:
            logger.info(f"Starting Phase 1: LLM tagging for {len(ambiguous_dedup_indices)} ambiguous cases...")

            for dedup_idx in ambiguous_dedup_indices:
                try:
                    row = dedup_df.loc[dedup_idx]
                    result = self.llm_tagger.tag_row(row, column_mapping)
                    cofog_results[dedup_idx] = result

                    if (ambiguous_dedup_indices.index(dedup_idx) + 1) % 10 == 0:
                        logger.info(f"LLM tagged {ambiguous_dedup_indices.index(dedup_idx) + 1}/{len(ambiguous_dedup_indices)} ambiguous cases")

                except Exception as e:
                    logger.error(f"LLM tagging failed for dedup row {dedup_idx}: {e}")

        # Expand results back to original rows
        logger.info("Expanding results back to all original rows...")
        expanded_results = dedup_helper.expand_results(cofog_results, mapping, len(df))

        # Apply results to original DataFrame
        for idx, result in enumerate(expanded_results):
            df.at[idx, "climate_tag"] = result.climate_tag.value
            df.at[idx, "confidence"] = result.confidence.value
            df.at[idx, "method"] = result.method
            df.at[idx, "environmental_objective"] = result.environmental_objective.value
            df.at[idx, "subcategory"] = result.subcategory or ""
            df.at[idx, "rationale"] = result.rationale

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

        print("\n" + "="*70)
        print("GREEN BUDGET TAGGING SUMMARY (Optimized)")
        print("="*70)

        # Classification breakdown
        print("\nClassification Breakdown:")
        for tag in ["GREEN", "NEUTRAL", "BROWN", "AMBIGUOUS"]:
            count = (df["climate_tag"] == tag).sum()
            pct = count / total * 100 if total > 0 else 0
            print(f"  {tag}: {count:,} ({pct:.1f}%)")

        # Confidence distribution
        print("\nConfidence Distribution:")
        for conf in ["HIGH", "MEDIUM", "LOW"]:
            count = (df["confidence"] == conf).sum()
            pct = count / total * 100 if total > 0 else 0
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

        print("="*70 + "\n")

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
        description="Green Budget Tagging System (Optimized) for World Bank BOOST Data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic COFOG-based tagging only (fast, free, 90% dedup savings)
  python green_budget_tagger_optimized.py input.xlsx -o output.xlsx

  # With LLM tagging for ambiguous cases (optimized with dedup)
  python green_budget_tagger_optimized.py input.xlsx -o output.xlsx --use-llm --api-key YOUR_KEY
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
    tagger = GreenBudgetTaggerOptimized(
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
