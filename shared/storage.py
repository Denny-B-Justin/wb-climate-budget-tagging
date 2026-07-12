import pandas as pd
import os
from pathlib import Path
from typing import List, Dict, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Base directory for processed data
DATA_ROOT = Path(__file__).parent.parent / "data" / "processed"

def ensure_storage():
    """Ensure the processed data directory exists."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

def get_storage_path(country: str, year: int, method: str) -> Path:
    """Generate a standard file path for a result."""
    # Clean inputs for filename
    country_clean = country.strip().upper().replace(" ", "_")
    method_clean = method.strip().upper().replace(" ", "_")
    filename = f"{country_clean}_{year}_{method_clean}.csv"
    return DATA_ROOT / filename

def save_result(df: pd.DataFrame, country: str, year: int, method: str) -> str:
    """Save tagging results to the database."""
    ensure_storage()
    file_path = get_storage_path(country, year, method)
    
    # Add metadata columns to the DF before saving if not already there
    df_to_save = df.copy()
    df_to_save["db_country"] = country
    df_to_save["db_year"] = year
    df_to_save["db_method"] = method
    df_to_save["db_saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    df_to_save.to_csv(file_path, index=False)
    logger.info(f"Saved result to {file_path}")
    return str(file_path)

def list_results() -> List[Dict]:
    """List all available results in the database."""
    ensure_storage()
    results = []
    
    for file in DATA_ROOT.glob("*.csv"):
        # Basic parsing from filename purely for display, 
        # but could also peek into the file for accuracy
        parts = file.stem.split("_")
        if len(parts) >= 3:
            results.append({
                "filename": file.name,
                "country": parts[0].replace("_", " "),
                "year": parts[1],
                "method": "_".join(parts[2:]).replace("_", " "),
                "mtime": datetime.fromtimestamp(file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            })
    
    # Sort by mtime descending
    return sorted(results, key=lambda x: x["mtime"], reverse=True)

def load_result(filename: str) -> pd.DataFrame:
    """Load a specific result from the database."""
    file_path = DATA_ROOT / filename
    if not file_path.exists():
        raise FileNotFoundError(f"Result file {filename} not found.")
    return pd.read_csv(file_path)

def result_exists(country: str, year: int, method: str) -> bool:
    """Check if a result already exists in the database."""
    return get_storage_path(country, year, method).exists()
