import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def _get_amount_column(df: pd.DataFrame, mapping: Optional[Dict] = None) -> Optional[str]:
    """Find the amount/budget column in dataframe."""
    # If a mapping is provided, check for explicitly set amount column
    if mapping:
        if "amount" in mapping:
            val = mapping["amount"]
            if val and val in df.columns:
                return val
            # If mapping["amount"] is None, the user explicitly skipped it
            if val is None:
                return None
        # If mapping is present but "amount" is missing, we fall back to auto-detection below
    
    # Auto-detection fallback
    amount_cols = ["amount", "budget", "expenditure", "value", "total", "amount_usd"]
    for col in amount_cols:
        if col in df.columns:
            return col
            
    return None


def calculate_percentage(df: pd.DataFrame, tag: str, mapping: Optional[Dict] = None) -> float:
    """Calculate percentage of total budget for a tag."""
    amount_col = _get_amount_column(df, mapping)

    if amount_col is None:
        logger.warning("No amount/budget column found in dataframe")
        return 0.0

    total = df[amount_col].sum()
    if total == 0:
        return 0.0

    tagged = df[df["climate_tag"] == tag][amount_col].sum()
    return (tagged / total) * 100


def calculate_amount(df: pd.DataFrame, tag: str, mapping: Optional[Dict] = None) -> float:
    """Calculate total amount for a tag."""
    amount_col = _get_amount_column(df, mapping)

    if amount_col is None:
        return 0.0

    return df[df["climate_tag"] == tag][amount_col].sum()


def calculate_total_amount(df: pd.DataFrame, mapping: Optional[Dict] = None) -> float:
    """Calculate total budget amount."""
    amount_col = _get_amount_column(df, mapping)

    if amount_col is None:
        return 0.0

    return df[amount_col].sum()


def get_top_ministries(df: pd.DataFrame, tag: str, n: int = 5, mapping: Optional[Dict] = None) -> pd.DataFrame:
    """
    Get top n ministries by spending for a tag.

    Returns a DataFrame with columns: ministry, amount, pct
    """
    # Find ministry column
    ministry_col = None
    if mapping:
        if "admin1" in mapping:
            val = mapping["admin1"]
            if val and val in df.columns:
                ministry_col = val
            elif val is None:
                # User explicitly skipped ministry
                return pd.DataFrame(columns=["ministry", "amount", "pct"])
    
    if ministry_col:
        df = df.copy()
        df["ministry"] = df[ministry_col]
    elif "ministry" not in df.columns:
        # Fallback to auto-detection if not explicitly mapped or skipped
        ministry_cols = ["ministry", "dept", "department", "admin1", "prog1"]
        for col in ministry_cols:
            if col in df.columns:
                df = df.copy()
                df["ministry"] = df[col]
                break
        else:
            logger.warning("No ministry column found")
            return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Find amount column
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None:
        logger.warning("No amount/budget column found")
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    tag_df = df[df["climate_tag"] == tag].copy()

    if len(tag_df) == 0:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Group by ministry and sum amounts
    top = tag_df.groupby("ministry")[amount_col].sum().nlargest(n).reset_index()
    top.columns = ["ministry", "amount"]

    # Calculate percentage of tag's total
    tag_total = tag_df[amount_col].sum()
    if tag_total > 0:
        top["pct"] = (top["amount"] / tag_total * 100).round(1)
    else:
        top["pct"] = 0.0

    # Format amount column
    top["amount_fmt"] = top["amount"].apply(
        lambda x: f"${x / 1e6:.0f}M" if x >= 1e6 else f"${x / 1e3:.0f}K"
    )

    return top[["ministry", "amount_fmt", "pct", "amount"]]


def filter_data(
    df: pd.DataFrame,
    tag_filter: Optional[List[str]] = None,
    confidence_filter: Optional[List[str]] = None,
    ministry_filter: Optional[List[str]] = None,
    method_filter: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Apply multiple filters to the dataframe.
    """
    filtered_df = df.copy()

    if tag_filter and "climate_tag" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["climate_tag"].isin(tag_filter)]

    if confidence_filter and "confidence" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["confidence"].isin(confidence_filter)]

    if ministry_filter and "ministry" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["ministry"].isin(ministry_filter)]

    if method_filter and "method" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["method"].isin(method_filter)]

    return filtered_df


def get_confidence_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """Get count of items by confidence level."""
    if "confidence" not in df.columns:
        return {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    return {
        "HIGH": len(df[df["confidence"] == "HIGH"]),
        "MEDIUM": len(df[df["confidence"] == "MEDIUM"]),
        "LOW": len(df[df["confidence"] == "LOW"]),
    }


def get_green_objectives_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """Get count of GREEN items by environmental objective."""
    green_df = df[df["climate_tag"] == "GREEN"]

    if "environmental_objective" not in green_df.columns:
        return {}

    # Filter out 'none' and empty values
    objectives = green_df[green_df["environmental_objective"].notna()]
    objectives = objectives[objectives["environmental_objective"] != "none"]
    objectives = objectives[objectives["environmental_objective"] != ""]

    return objectives["environmental_objective"].value_counts().to_dict()


def get_method_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """Get count of items by tagging method."""
    if "method" not in df.columns:
        return {}

    return df["method"].value_counts().to_dict()


def format_number(num: float, suffix: str = "") -> str:
    """Format large numbers for display."""
    if num >= 1e9:
        return f"{num / 1e9:.1f}B{suffix}"
    elif num >= 1e6:
        return f"{num / 1e6:.1f}M{suffix}"
    elif num >= 1e3:
        return f"{num / 1e3:.1f}K{suffix}"
    else:
        return f"{num:.0f}{suffix}"


def validate_dataframe(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Validate that dataframe has required columns for dashboard.

    Returns:
        (is_valid, error_message)
    """
    required_columns = ["climate_tag"]

    for col in required_columns:
        if col not in df.columns:
            return False, f"Missing required column: {col}"

    return True, ""


# =============================================================================
# RIO MARKER METRICS
# =============================================================================


def get_rio_marker_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """
    Get count of items by Rio Marker level (0, 1, 2).

    Args:
        df: DataFrame with rio_marker column

    Returns:
        Dict with marker labels as keys and counts as values
    """
    if "rio_marker" not in df.columns:
        return {"Not Targeted": 0, "Significant Objective": 0, "Principal Objective": 0}

    return {
        "Not Targeted": (df["rio_marker"] == 0).sum(),
        "Significant Objective": (df["rio_marker"] == 1).sum(),
        "Principal Objective": (df["rio_marker"] == 2).sum(),
    }


def get_rio_mitigation_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """
    Get count of items by Rio Mitigation marker level (0, 1, 2).

    Args:
        df: DataFrame with rio_mitigation column

    Returns:
        Dict with marker labels as keys and counts as values
    """
    if "rio_mitigation" not in df.columns:
        return {"Not Targeted": 0, "Significant Objective": 0, "Principal Objective": 0}

    return {
        "Not Targeted": (df["rio_mitigation"] == 0).sum(),
        "Significant Objective": (df["rio_mitigation"] == 1).sum(),
        "Principal Objective": (df["rio_mitigation"] == 2).sum(),
    }


def get_rio_adaptation_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """
    Get count of items by Rio Adaptation marker level (0, 1, 2).

    Args:
        df: DataFrame with rio_adaptation column

    Returns:
        Dict with marker labels as keys and counts as values
    """
    if "rio_adaptation" not in df.columns:
        return {"Not Targeted": 0, "Significant Objective": 0, "Principal Objective": 0}

    return {
        "Not Targeted": (df["rio_adaptation"] == 0).sum(),
        "Significant Objective": (df["rio_adaptation"] == 1).sum(),
        "Principal Objective": (df["rio_adaptation"] == 2).sum(),
    }


def calculate_rio_weighted_amount(df: pd.DataFrame, mapping: Optional[Dict] = None) -> float:
    """
    Calculate total climate-weighted amount using Rio Markers.
    Weighting: Principal (2) -> 100%, Significant (1) -> 40%, Not Targeted (0) -> 0%
    """
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None or "rio_marker" not in df.columns:
        return 0.0

    # Apply weights
    weights = df["rio_marker"].map({2: 1.0, 1: 0.4, 0: 0.0}).fillna(0.0)
    return (df[amount_col] * weights).sum()


def calculate_rio_weighted_percentage(df: pd.DataFrame, mapping: Optional[Dict] = None) -> float:
    """Calculate percentage of total budget that is climate-weighted."""
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None:
        return 0.0

    total = df[amount_col].sum()
    if total == 0:
        return 0.0

    weighted_amount = calculate_rio_weighted_amount(df, mapping)
    return (weighted_amount / total) * 100


def compare_cofog_rio(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare COFOG and Rio Marker tagging results.

    Creates a DataFrame with discrepancies tagged.

    Args:
        df: DataFrame with both cofog and rio marker results

    Returns:
        DataFrame with comparison columns
    """
    required_cols = ["climate_tag", "rio_marker"]

    # Check if both COFOG and Rio results exist
    # Note: We'll use climate_tag for COFOG (when method=cofog)
    # and rio_marker for Rio Marker tagging

    if not all(col in df.columns for col in required_cols):
        return pd.DataFrame()

    # Create comparison DataFrame
    comparison = pd.DataFrame(
        {
            "index": df.index,
            "cofog_tag": df["climate_tag"],
            "rio_marker": df["rio_marker"],
            "discrepancy": df["climate_tag"]
            != df["climate_tag"],  # Will be updated based on mapping
        }
    )

    # Map rio_marker to climate_tag for comparison
    # Rio Marker 2 (Principal) -> GREEN
    # Rio Marker 1 (Significant) -> GREEN
    # Rio Marker 0 (Not Targeted) -> NEUTRAL

    rio_to_climate = {2: "GREEN", 1: "GREEN", 0: "NEUTRAL"}

    comparison["rio_climate_tag"] = df["rio_marker"].map(rio_to_climate)
    comparison["discrepancy"] = comparison["cofog_tag"] != comparison["rio_climate_tag"]

    return comparison


def get_rio_top_ministries(
    df: pd.DataFrame, tag: str = "rio_marker", n: int = 5, mapping: Optional[Dict] = None
) -> pd.DataFrame:
    """
    Get top n ministries by Rio Marker level.

    Args:
        df: DataFrame with rio_marker column
        tag: Which marker to use ('rio_marker', 'rio_mitigation', or 'rio_adaptation')
        n: Number of top items to return
        mapping: Optional column mapping

    Returns:
        DataFrame with ministry, amount, pct columns
    """
    if tag not in df.columns:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Find ministry column
    ministry_col = mapping.get("admin1") if mapping else None
    
    if ministry_col and ministry_col in df.columns:
        df = df.copy()
        df["ministry"] = df[ministry_col]
    elif "ministry" not in df.columns:
        ministry_cols = ["ministry", "dept", "department", "admin1", "prog1"]
        for col in ministry_cols:
            if col in df.columns:
                df = df.copy()
                df["ministry"] = df[col]
                break
        else:
            return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Find amount column
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Group by ministry and marker level
    tag_df = df[df[tag] == 2]  # Principal objective

    if len(tag_df) == 0:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Group by ministry and sum amounts
    top = tag_df.groupby("ministry")[amount_col].sum().nlargest(n).reset_index()
    top.columns = ["ministry", "amount"]

    # Calculate percentage of principal objective spending
    tag_total = tag_df[amount_col].sum()
    if tag_total > 0:
        top["pct"] = (top["amount"] / tag_total * 100).round(1)
    else:
        top["pct"] = 0.0

    # Format amount column
    top["amount_fmt"] = top["amount"].apply(
        lambda x: f"${x / 1e6:.0f}M" if x >= 1e6 else f"${x / 1e3:.0f}K"
    )

    return top[["ministry", "amount_fmt", "pct", "amount"]]


# =============================================================================
# EU TAXONOMY METRICS
# =============================================================================


def get_eu_taxonomy_distribution(df: pd.DataFrame) -> Dict[str, int]:
    """
    Get count of items by each of the 6 EU Taxonomy objectives.
    """
    objectives = [
        "mitigation", "adaptation", "water", "circular_economy", "pollution", "biodiversity"
    ]
    
    results = {}
    for obj in objectives:
        col_name = f"eu_{obj}"
        if col_name in df.columns:
            results[obj] = df[col_name].sum()
        else:
            results[obj] = 0
            
    return results


def get_eu_top_ministries(df: pd.DataFrame, objective: str, n: int = 5, mapping: Optional[Dict] = None) -> pd.DataFrame:
    """
    Get top n ministries for a specific EU objective.
    """
    col_name = f"eu_{objective}"
    if col_name not in df.columns:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Find ministry column
    ministry_col = mapping.get("admin1") if mapping else None
    
    if ministry_col and ministry_col in df.columns:
        df = df.copy()
        df["ministry"] = df[ministry_col]
    elif "ministry" not in df.columns:
        ministry_cols = ["ministry", "dept", "department", "admin1", "prog1"]
        for col in ministry_cols:
            if col in df.columns:
                df = df.copy()
                df["ministry"] = df[col]
                break
        else:
            return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Find amount column
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Filter by rows where the objective is True
    tag_df = df[df[col_name] == True]

    if len(tag_df) == 0:
        return pd.DataFrame(columns=["ministry", "amount", "pct"])

    # Group and sum
    top = tag_df.groupby("ministry")[amount_col].sum().nlargest(n).reset_index()
    top.columns = ["ministry", "amount"]

    # Calculate percentage
    total = tag_df[amount_col].sum()
    if total > 0:
        top["pct"] = (top["amount"] / total * 100).round(1)
    else:
        top["pct"] = 0.0

    # Format
    top["amount_fmt"] = top["amount"].apply(
        lambda x: f"${x / 1e6:.0f}M" if x >= 1e6 else f"${x / 1e3:.0f}K"
    )

    return top[["ministry", "amount_fmt", "pct", "amount"]]


def get_sectoral_breakdown(df: pd.DataFrame, n: int = 10, mapping: Optional[Dict] = None) -> pd.DataFrame:
    """Get budget totals and percentages by sector (func1)."""
    sector_col = mapping.get("func1") if mapping else "func1"
    if sector_col not in df.columns:
        # Fallback
        for col in ["func1", "sector", "function"]:
            if col in df.columns:
                sector_col = col
                break
        else:
            return pd.DataFrame(columns=["sector", "amount", "pct"])

    amount_col = _get_amount_column(df, mapping)
    if amount_col is None:
        return pd.DataFrame(columns=["sector", "amount", "pct"])

    # Consider only climate-relevant (Green) items for the breakdown
    green_df = df[df["climate_tag"] == "GREEN"]
    if green_df.empty:
        return pd.DataFrame(columns=["sector", "amount", "pct"])

    total_green = green_df[amount_col].sum()
    breakdown = green_df.groupby(sector_col)[amount_col].sum().nlargest(n).reset_index()
    breakdown.columns = ["sector", "amount"]
    breakdown["pct"] = (breakdown["amount"] / total_green * 100).round(1)
    
    return breakdown


def get_objective_split(df: pd.DataFrame, mapping: Optional[Dict] = None) -> Dict[str, float]:
    """Calculate share of Mitigation, Adaptation, and Cross-cutting."""
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None:
        return {"mitigation": 0, "adaptation": 0, "crosscutting": 0}

    green_df = df[df["climate_tag"] == "GREEN"]
    total_green = green_df[amount_col].sum()
    if total_green == 0:
        return {"mitigation": 0, "adaptation": 0, "crosscutting": 0}

    # Mitigation / Adaptation / Cross-cutting
    if "rio_mitigation" in df.columns and "rio_adaptation" in df.columns:
        mit = green_df[(green_df["rio_mitigation"] > 0) & (green_df["rio_adaptation"] == 0)][amount_col].sum()
        ada = green_df[(green_df["rio_mitigation"] == 0) & (green_df["rio_adaptation"] > 0)][amount_col].sum()
        cross = green_df[(green_df["rio_mitigation"] > 0) & (green_df["rio_adaptation"] > 0)][amount_col].sum()
    elif "primary_objective" in df.columns:
        mit = green_df[green_df["primary_objective"] == "mitigation"][amount_col].sum()
        ada = green_df[green_df["primary_objective"] == "adaptation"][amount_col].sum()
        # simplified fallback for EU
        cross = total_green - mit - ada
    else:
        return {"mitigation": 0, "adaptation": 0, "crosscutting": 0}

    return {
        "mitigation": (mit / total_green * 100).round(1),
        "adaptation": (ada / total_green * 100).round(1),
        "crosscutting": (cross / total_green * 100).round(1)
    }


def calculate_trend_simple(df: pd.DataFrame, year_col: str = "year", mapping: Optional[Dict] = None) -> float:
    """Calculate percentage point change between first and last year."""
    amount_col = _get_amount_column(df, mapping)
    if amount_col is None or year_col not in df.columns:
        return 0.0

    years = sorted(df[year_col].unique())
    if len(years) < 2:
        return 0.0

    def get_share(y):
        y_df = df[df[year_col] == y]
        total = y_df[amount_col].sum()
        if total == 0: return 0
        green = y_df[y_df["climate_tag"] == "GREEN"][amount_col].sum()
        return (green / total) * 100

    return round(get_share(years[-1]) - get_share(years[0]), 2)


def get_capital_share(df: pd.DataFrame, mapping: Optional[Dict] = None) -> float:
    """Calculate share of capital expenditure in green budget."""
    econ_col = mapping.get("econ1") if mapping else "econ1"
    amount_col = _get_amount_column(df, mapping)
    
    if amount_col is None or econ_col not in df.columns:
        return 0.0

    green_df = df[df["climate_tag"] == "GREEN"]
    if green_df.empty:
        return 0.0

    capital_keywords = ["capital", "investment", "development", "project"]
    is_capital = green_df[econ_col].astype(str).str.lower().apply(
        lambda x: any(k in x for k in capital_keywords)
    )
    
    capital_total = green_df[is_capital][amount_col].sum()
    total_green = green_df[amount_col].sum()
    
    return round((capital_total / total_green * 100), 1) if total_green > 0 else 0.0
