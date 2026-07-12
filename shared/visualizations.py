import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from typing import Dict, Any, Optional
import logging
from shared.metrics import _get_amount_column

logger = logging.getLogger(__name__)


def plot_budget_donut(df: pd.DataFrame, mapping: Optional[Dict] = None) -> go.Figure:
    """
    Create a donut chart showing budget composition by climate tag.

    Returns:
        Plotly figure object
    """
    amount_col = _get_amount_column(df, mapping)

    if amount_col is None or "climate_tag" not in df.columns:
        logger.warning("Required columns for donut chart not found")
        return _create_empty_chart("Budget Composition")

    # Group by climate tag and sum amounts
    tag_amounts = df.groupby("climate_tag")[amount_col].sum()
    total = tag_amounts.sum()

    if total == 0:
        return _create_empty_chart("Budget Composition (No Data)")

    # Create donut chart
    colors = {"GREEN": "#4CAF50", "NEUTRAL": "#9E9E9E", "BROWN": "#795548"}

    fig = go.Figure(
        data=[
            go.Pie(
                labels=tag_amounts.index,
                values=tag_amounts.values,
                hole=0.4,
                marker=dict(
                    colors=[colors.get(tag, "#999999") for tag in tag_amounts.index]
                ),
                textinfo="label+percent",
                textfont_size=14,
                hovertemplate="<b>%{label}</b><br>Amount: $%{value:,.0f}<br>Percentage: %{percent}<extra></extra>",
            )
        ]
    )

    # Update layout
    fig.update_layout(
        title={
            "text": "Budget Composition by Climate Tag",
            "font": {"size": 18, "color": "#2c3e50"},
        },
        showlegend=True,
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
        font=dict(size=12),
    )

    return fig


def plot_green_objectives(df: pd.DataFrame) -> go.Figure:
    """
    Create a horizontal bar chart showing green items by environmental objective.

    Returns:
        Plotly figure object
    """
    green_df = df[df["climate_tag"] == "GREEN"]

    if "environmental_objective" not in green_df.columns or len(green_df) == 0:
        return _create_empty_chart("Green Objectives Distribution")

    # Filter out 'none' and empty values
    objectives = green_df[green_df["environmental_objective"].notna()]
    objectives = objectives[objectives["environmental_objective"] != "none"]
    objectives = objectives[objectives["environmental_objective"] != ""]

    if len(objectives) == 0:
        return _create_empty_chart("Green Objectives Distribution (No Data)")

    # Count by objective
    obj_counts = (
        objectives["environmental_objective"].value_counts().sort_values(ascending=True)
    )

    fig = go.Figure(
        data=[
            go.Bar(
                y=obj_counts.index,
                x=obj_counts.values,
                orientation="h",
                marker_color="#4CAF50",
            )
        ]
    )

    fig.update_layout(
        title={
            "text": "Green Items by Environmental Objective",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Count",
        yaxis_title="Objective",
        height=max(300, len(obj_counts) * 30 + 100),
        margin=dict(l=150, r=20, t=40, b=40),
        font=dict(size=11),
    )

    return fig


def plot_methods(df: pd.DataFrame) -> go.Figure:
    """
    Create a horizontal bar chart showing tagging methods.

    Returns:
        Plotly figure object
    """
    if "method" not in df.columns or len(df) == 0:
        return _create_empty_chart("Tagging Methods")

    # Count by method
    method_counts = df["method"].value_counts().sort_values(ascending=True)

    fig = go.Figure(
        data=[
            go.Bar(
                y=method_counts.index,
                x=method_counts.values,
                orientation="h",
                marker_color="#2196F3",
            )
        ]
    )

    fig.update_layout(
        title={
            "text": "Tagging Methods Used",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Count",
        yaxis_title="Method",
        height=max(300, len(method_counts) * 30 + 100),
        margin=dict(l=150, r=20, t=40, b=40),
        font=dict(size=11),
    )

    return fig


def plot_confidence_distribution(df: pd.DataFrame) -> go.Figure:
    """
    Create a bar chart showing confidence distribution.

    Returns:
        Plotly figure object
    """
    if "confidence" not in df.columns or len(df) == 0:
        return _create_empty_chart("Confidence Distribution")

    # Count by confidence
    conf_counts = df["confidence"].value_counts()

    # Ensure all levels are present
    for level in ["HIGH", "MEDIUM", "LOW"]:
        if level not in conf_counts.index:
            conf_counts[level] = 0

    # Sort order
    conf_counts = conf_counts[["HIGH", "MEDIUM", "LOW"]]

    colors = {"HIGH": "#4CAF50", "MEDIUM": "#FFC107", "LOW": "#F44336"}

    fig = go.Figure(
        data=[
            go.Bar(
                x=conf_counts.index,
                y=conf_counts.values,
                marker_color=[colors[level] for level in conf_counts.index],
            )
        ]
    )

    fig.update_layout(
        title={
            "text": "Confidence Distribution",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Confidence Level",
        yaxis_title="Count",
        height=300,
        margin=dict(l=20, r=20, t=40, b=40),
        font=dict(size=11),
    )

    return fig


def plot_year_trend(df: pd.DataFrame, mapping: Optional[Dict] = None) -> go.Figure:
    """
    Create a line chart showing green spending trend over years.

    Returns:
        Plotly figure object
    """
    amount_col = _get_amount_column(df, mapping)
    year_col = mapping.get("year") if mapping else "year"
    if year_col not in df.columns: year_col = "year"

    if year_col not in df.columns or amount_col is None or len(df) == 0:
        return _create_empty_chart("Year-over-Year Trend")

    # Group by year and tag
    year_tag = df.groupby([year_col, "climate_tag"])[amount_col].sum().reset_index()

    fig = go.Figure()

    colors = {"GREEN": "#4CAF50", "NEUTRAL": "#9E9E9E", "BROWN": "#795548"}

    for tag in ["GREEN", "NEUTRAL", "BROWN"]:
        tag_data = year_tag[year_tag["climate_tag"] == tag]
        if len(tag_data) > 0:
            fig.add_trace(
                go.Scatter(
                    x=tag_data[year_col],
                    y=tag_data[amount_col],
                    mode="lines+markers",
                    name=tag,
                    line=dict(color=colors.get(tag, "#999999"), width=2),
                    marker=dict(size=8),
                )
            )

    fig.update_layout(
        title={
            "text": "Year-over-Year Spending by Climate Tag",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Year",
        yaxis_title="Amount ($)",
        height=400,
        margin=dict(l=20, r=20, t=40, b=40),
        font=dict(size=11),
        hovermode="x unified",
    )

    return fig


def _create_empty_chart(title: str) -> go.Figure:
    """Create an empty chart with a message."""
    fig = go.Figure()

    fig.add_annotation(
        x=0.5,
        y=0.5,
        text="No data available",
        showarrow=False,
        font=dict(size=16, color="#999999"),
    )

    fig.update_layout(
        title={
            "text": title,
            "font": {"size": 14, "color": "#2c3e50"},
        },
        height=300,
        xaxis=dict(showgrid=False, showticklabels=False),
        yaxis=dict(showgrid=False, showticklabels=False),
        margin=dict(l=20, r=20, t=40, b=20),
    )

    return fig


# =============================================================================
# RIO MARKER VISUALIZATIONS
# =============================================================================


def plot_rio_marker_distribution(df: pd.DataFrame) -> go.Figure:
    """
    Create a bar chart showing Rio Marker distribution (0, 1, 2).

    Args:
        df: DataFrame with rio_marker column

    Returns:
        Plotly figure object
    """
    if "rio_marker" not in df.columns:
        return _create_empty_chart("Rio Marker Distribution")

    marker_counts = df["rio_marker"].value_counts().sort_index()
    marker_labels = {
        0: "Not Targeted",
        1: "Significant Objective",
        2: "Principal Objective",
    }

    # Create label list
    x_labels = [marker_labels.get(k, str(k)) for k in marker_counts.index]

    # Colors: Gray, Yellow, Green
    colors = ["#9E9E9E", "#FFC107", "#4CAF50"]
    marker_colors = [colors[i % len(colors)] for i in range(len(marker_counts.index))]

    fig = go.Figure(
        data=[
            go.Bar(
                x=x_labels,
                y=marker_counts.values,
                marker_color=marker_colors,
            )
        ]
    )

    fig.update_layout(
        title={
            "text": "Rio Marker Distribution",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Rio Marker",
        yaxis_title="Count",
        height=400,
        margin=dict(l=20, r=20, t=40, b=40),
        font=dict(size=11),
    )

    return fig


def plot_rio_mitigation_adaptation(df: pd.DataFrame, mapping: Optional[Dict] = None) -> go.Figure:
    """
    Create a grouped bar chart showing Rio Mitigation and Adaptation distribution.

    Args:
        df: DataFrame with rio_mitigation and rio_adaptation columns
        mapping: Optional column mapping

    Returns:
        Plotly figure object
    """
    if "rio_mitigation" not in df.columns or "rio_adaptation" not in df.columns:
        return _create_empty_chart("Rio Mitigation vs Adaptation")

    # Count by mitigation level
    mitigation_counts = df["rio_mitigation"].value_counts().sort_index()

    # Count by adaptation level
    adaptation_counts = df["rio_adaptation"].value_counts().sort_index()

    # Marker labels
    marker_labels = {
        0: "Not Targeted",
        1: "Significant Objective",
        2: "Principal Objective",
    }

    x_labels = [
        marker_labels.get(k, str(k))
        for k in sorted(set(mitigation_counts.index) | set(adaptation_counts.index))
    ]

    fig = go.Figure(
        data=[
            go.Bar(
                name="Mitigation",
                x=x_labels,
                y=[
                    mitigation_counts.get(k, 0)
                    for k in sorted(
                        set(mitigation_counts.index) | set(adaptation_counts.index)
                    )
                ],
                marker_color="#4CAF50",  # Green
            ),
            go.Bar(
                name="Adaptation",
                x=x_labels,
                y=[
                    adaptation_counts.get(k, 0)
                    for k in sorted(
                        set(mitigation_counts.index) | set(adaptation_counts.index)
                    )
                ],
                marker_color="#2196F3",  # Blue
            ),
        ]
    )

    fig.update_layout(
        title={
            "text": "Rio Mitigation vs Adaptation",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Rio Marker Level",
        yaxis_title="Count",
        barmode="group",
        height=400,
        margin=dict(l=20, r=20, t=40, b=40),
        font=dict(size=11),
        legend=dict(x=0, y=1),
    )

    return fig


# =============================================================================
# EU TAXONOMY VISUALIZATIONS
# =============================================================================


def plot_eu_taxonomy_objectives(df: pd.DataFrame, mapping: Optional[Dict] = None) -> go.Figure:
    """
    Create a horizontal bar chart showing distribution across the 6 EU objectives.
    """
    objectives = {
        "eu_mitigation": "Mitigation",
        "eu_adaptation": "Adaptation",
        "eu_water": "Water & Marine",
        "eu_circular_economy": "Circular Economy",
        "eu_pollution": "Pollution Control",
        "eu_biodiversity": "Biodiversity"
    }
    
    counts = []
    labels = []
    
    for col, label in objectives.items():
        if col in df.columns:
            counts.append(df[col].sum())
            labels.append(label)
            
    if not counts or sum(counts) == 0:
        return _create_empty_chart("EU Taxonomy Objectives Distribution")

    # Sort by count
    data = pd.DataFrame({"label": labels, "count": counts}).sort_values("count")

    fig = go.Figure(
        data=[
            go.Bar(
                y=data["label"],
                x=data["count"],
                orientation="h",
                marker_color="#4CAF50",
            )
        ]
    )

    fig.update_layout(
        title={
            "text": "EU Taxonomy Environmental Objectives",
            "font": {"size": 14, "color": "#2c3e50"},
        },
        xaxis_title="Count of Budget Lines",
        yaxis_title="Objective",
        height=400,
        margin=dict(l=150, r=20, t=40, b=40),
        font=dict(size=11),
    )

    return fig
