"""
Green Budget Tagging System - Main Application

Single-page application with role-based views using session state navigation.
Includes both Analyst Dashboard (full-featured) and Executive Dashboard (minimal).
"""

import streamlit as st
import pandas as pd
import io
import os
import tempfile
from pathlib import Path
import logging
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent))

from green_budget_tagger import ExcelProcessor, WordProcessor
from green_budget_tagger_optimized import GreenBudgetTaggerOptimized
from eu_taxonomy_tagger import EUTaxonomyTagger
from rio_marker_tagger import RioMarkerTagger
from shared.metrics import (
    calculate_percentage,
    calculate_amount,
    calculate_total_amount,
    get_top_ministries,
    filter_data,
    get_confidence_distribution,
    get_green_objectives_distribution,
    get_method_distribution,
    validate_dataframe,
    format_number,
    get_rio_marker_distribution,
    get_rio_mitigation_distribution,
    get_rio_adaptation_distribution,
    compare_cofog_rio,
    get_rio_top_ministries,
    get_eu_taxonomy_distribution,
    get_eu_top_ministries,
    calculate_rio_weighted_percentage,
    calculate_rio_weighted_amount,
    get_sectoral_breakdown,
    get_objective_split,
    calculate_trend_simple,
    get_capital_share,
    _get_amount_column,
)
from shared.report_generator import ReportGenerator
from shared.visualizations import (
    plot_budget_donut,
    plot_green_objectives,
    plot_methods,
    plot_confidence_distribution,
    plot_year_trend,
    plot_rio_marker_distribution,
    plot_rio_mitigation_adaptation,
    plot_eu_taxonomy_objectives,
)
from shared.storage import (
    save_result,
    list_results,
    load_result,
    result_exists,
)
from shared.rio_keywords import get_rio_marker_label

# Configure page
st.set_page_config(
    page_title="Green Budget Tagging",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
logger.info("Initializing Green Budget Tagging application")

# Custom CSS
st.markdown(
    """
    <style>
    .metric-container {
        display: flex;
        gap: 20px;
        margin: 20px 0;
    }
    .metric-box {
        flex: 1;
        padding: 20px;
        border-radius: 8px;
        background-color: #f0f2f6;
    }
    .metric-green {
        border-left: 4px solid #4caf50;
    }
    .metric-neutral {
        border-left: 4px solid #9e9e9e;
    }
    .metric-brown {
        border-left: 4px solid #795548;
    }
    .headline-box {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 30px;
        border-radius: 12px;
        color: white;
        margin-bottom: 30px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .headline-text {
        font-size: 28px;
        font-weight: 600;
        margin-bottom: 10px;
    }
    .headline-context {
        font-size: 18px;
        opacity: 0.95;
    }
    .kpi-box {
        background: white;
        padding: 30px 20px;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        text-align: center;
        border-top: 4px solid #667eea;
    }
    .kpi-green {
        border-top-color: #4CAF50;
    }
    .kpi-value-green {
        color: #4CAF50;
    }
    .kpi-neutral {
        border-top-color: #9E9E9E;
    }
    .kpi-value-neutral {
        color: #9E9E9E;
    }
    .kpi-brown {
        border-top-color: #795548;
    }
    .kpi-value-brown {
        color: #795548;
    }
    .ministry-table {
        background: white;
        padding: 25px;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    .recommendation-box {
        background: #fff3e0;
        border-left: 4px solid #ff9800;
        padding: 25px;
        border-radius: 8px;
        margin-bottom: 30px;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# Initialize session state
if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "processing_complete" not in st.session_state:
    st.session_state.processing_complete = False
if "tagged_file" not in st.session_state:
    st.session_state.tagged_file = None
if "results_with_comments" not in st.session_state:
    st.session_state.results_with_comments = None
if "current_view" not in st.session_state:
    st.session_state.current_view = "landing"
if "db_country" not in st.session_state:
    st.session_state.db_country = "Global"
if "db_year" not in st.session_state:
    st.session_state.db_year = datetime.now().year
if "analyst_tab" not in st.session_state:
    st.session_state.analyst_tab = "📤 Upload & Tag"
if "column_mapping" not in st.session_state:
    st.session_state.column_mapping = {}
# Note: tagging_method is automatically managed by st.radio() with key="tagging_method"
# No need to initialize it here since radio widget has its own default


def show_landing_page():
    """Display the landing page with role selection."""

    logger.info("Displaying landing page")

    st.markdown(
        """
        <div style="text-align: center; padding: 60px 20px;">
            <h1 style="font-size: 56px; font-weight: 800; margin-bottom: 20px; background: linear-gradient(90deg, #1a237e, #4527a0); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Green Budget Tagging Platform</h1>
            <p style="font-size: 22px; color: #546e7a; margin-bottom: 40px; font-weight: 400; max-width: 800px; margin-left: auto; margin-right: auto;">Advanced institutional framework for automated budget alignment with Global Green Taxonomies and Rio Conventions.</p>
        </div>
    """,
        unsafe_allow_html=True,
    )

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """
            <div style="padding: 40px; border-radius: 12px; border: 2px solid #e0e0e0; min-height: 300px; text-align: center;">
                <div style="font-size: 72px; margin-bottom: 20px;">🏛️</div>
                <div style="font-size: 28px; font-weight: 700; margin-bottom: 12px; color: #1a237e;">Executive Monitoring Dashboard</div>
                <div style="font-size: 16px; color: #666; line-height: 1.6; margin-bottom: 24px;">
                    Minimal, visual, and outcome-oriented. Perfect for ministers and executives who need quick insights in under 60 seconds.
                </div>
                <div style="font-size: 14px; color: #888; text-align: left;">
                    <ul>
                        <li>✅ One headline insight</li>
                        <li>✅ KPI tiles at a glance</li>
                        <li>✅ Top 5 ministries by spending</li>
                        <li>✅ No technical details</li>
                    </ul>
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )

        if st.button(
            "🏛️ Access Executive Suite",
            type="primary",
            use_container_width=True,
            key="exec_entry",
        ):
            st.session_state.current_view = "executive"
            st.rerun()

    with col2:
        st.markdown(
            """
            <div style="padding: 40px; border-radius: 12px; border: 2px solid #e0e0e0; min-height: 300px; text-align: center;">
                <div style="font-size: 72px; margin-bottom: 20px;">🔬</div>
                <div style="font-size: 28px; font-weight: 700; margin-bottom: 12px; color: #1a237e;">Policy Analyst Workspace</div>
                <div style="font-size: 16px; color: #666; line-height: 1.6; margin-bottom: 24px;">
                    Full-featured technical dashboard with filters, detailed tables, and data editing. For researchers and policy experts.
                </div>
                <div style="font-size: 14px; color: #888; text-align: left;">
                    <ul>
                        <li>✅ Upload and process files</li>
                        <li>✅ Advanced filters</li>
                        <li>✅ Editable tables with comments</li>
                        <li>✅ Downloadable reports</li>
                    </ul>
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )

        if st.button(
            "🔬 Access Analyst Workspace", use_container_width=True, key="analyst_entry"
        ):
            st.session_state.current_view = "analyst"
            st.rerun()


def show_executive_dashboard():
    """Display minimal executive dashboard."""

    logger.info("Rendering executive dashboard")

    # Header with navigation
    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown(
            '<h1 style="color: #1a237e; font-weight: 800;">🌍 Executive Monitoring Dashboard</h1>',
            unsafe_allow_html=True,
        )
    with col2:
        if st.button("🏠 Home", use_container_width=True):
            st.session_state.current_view = "landing"
            st.rerun()

    # Database Selection & Search
    available_results = list_results()
    logger.info("Loaded %d available result records for executive dashboard", len(available_results))
    
    col_s1, col_s2 = st.columns([3, 1])
    with col_s1:
        search_query = st.text_input("🔍 Search Intelligence Database", placeholder="Search by country, year or method...")
    
    filtered_results = available_results
    if search_query:
        filtered_results = [
            r for r in available_results 
            if search_query.lower() in r.get('country', '').lower() 
            or search_query.lower() in str(r.get('year', '')).lower()
            or search_query.lower() in r.get('method', '').lower()
        ]
        logger.info("Executive search query '%s' matched %d results", search_query, len(filtered_results))
    
    selected_db_file = None
    if filtered_results:
        db_options = ["None (Upload new data in Analyst Workspace)"] + [
            f"{r['country']} ({r['year']}) - {r['method']}" for r in filtered_results
        ]
        db_filenames = [None] + [r['filename'] for r in filtered_results]
        
        selected_index = st.selectbox(
            f"📈 Found {len(filtered_results)} projects in database",
            options=range(len(db_options)),
            format_func=lambda x: db_options[x],
            help="Browse previously processed and approved budget results"
        )
        
        if selected_index > 0:
            selected_db_file = db_filenames[selected_index]
            df = load_result(selected_db_file)
            st.session_state.results_df = df
            logger.info("Loaded executive result set from database file '%s'", selected_db_file)
            st.info(f"📈 Showing verified findings for **{available_results[selected_index-1]['country']} ({available_results[selected_index-1]['year']})**")
    
    # Load data from session state if not selected from DB
    if selected_db_file is None:
        df = st.session_state.get("results_df")

    # If no data, show message
    if df is None:
        logger.warning("No executive result data available in session state")
        if not available_results:
            st.warning(
                "⚠️ The intelligence database is currently empty. Please process budget data in the Analyst Workspace first."
            )
        else:
            st.info("💡 Select a project from the database above or switch to the Analyst Workspace to upload new data.")
            
        if st.button("🔬 Go to Policy Analyst Workspace", use_container_width=True):
            st.session_state.current_view = "analyst"
            st.rerun()
        return

    # Check if amount column exists
    from shared.metrics import _get_amount_column

    amount_col = _get_amount_column(df)

    if amount_col is None:
        logger.warning("No amount column found in executive result data; KPI amounts will be skipped")
        st.warning(
            "⚠️ No amount/budget column found in your data. KPI amounts will not be displayed. Please ensure your Excel file has a column named 'amount', 'budget', 'expenditure', or 'value'."
        )

    # Calculate metrics
    if df.get("method", [""])[0] == "Rio Markers" or "rio_marker" in df.columns:
        green_pct = calculate_rio_weighted_percentage(df)
        green_amount = calculate_rio_weighted_amount(df)
    else:
        green_pct = calculate_percentage(df, "GREEN")
        green_amount = calculate_amount(df, "GREEN")
        
    neutral_pct = calculate_percentage(df, "NEUTRAL")
    neutral_amount = calculate_amount(df, "NEUTRAL")
    brown_pct = calculate_percentage(df, "BROWN")
    brown_amount = calculate_amount(df, "BROWN")
    total_amount = calculate_total_amount(df)
    target_pct = 10.0  # 10% target

    # Headline insight
    if green_pct >= target_pct:
        status_color = "#4CAF50"
        status_icon = "✅"
        status_text = "exceeding"
    elif green_pct >= target_pct * 0.8:
        status_color = "#FFC107"
        status_icon = "⚠️"
        status_text = "approaching"
    else:
        status_color = "#F44336"
        status_icon = "🔴"
        status_text = "below"

    gap = abs(target_pct - green_pct)

    # Extract country name from data if available
    country_name = "the selected country"
    if "admin1" in df.columns:
        first_admin = df["admin1"].iloc[0] if len(df) > 0 else None
        if first_admin:
            country_name = str(first_admin)

    if amount_col is not None:
        st.markdown(
            f"""
            <div class="headline-box">
                <div class="headline-text">{status_icon} Green spending represents {green_pct:.1f}% of total budget for {country_name}</div>
                <div class="headline-context">
                    ${format_number(green_amount)} — {status_text} {target_pct:.0f}% international target by {gap * 100:.1f} percentage points
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="headline-box">
                <div class="headline-text">{status_icon} Green spending represents {green_pct:.1f}% of total budget items</div>
                <div class="headline-context">
                    {status_text} {target_pct:.0f}% international target by {gap * 100:.1f} percentage points
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    # KPI tiles
    col1, col2, col3, col4 = st.columns(4)

    # Determine whether to show amounts
    show_amounts = amount_col is not None

    with col1:
        st.markdown(
            f"""
            <div class="kpi-box kpi-green">
                <div style="font-size: 14px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">GREEN</div>
                <div class="kpi-value kpi-value-green" style="font-size: 36px; font-weight: 700; margin-bottom: 8px;">{green_pct:.1f}%</div>
                <div style="font-size: 16px; color: #888;">{"$" + format_number(green_amount) if show_amounts else "No amount data"}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="kpi-box kpi-neutral">
                <div style="font-size: 14px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">NEUTRAL</div>
                <div class="kpi-value kpi-value-neutral" style="font-size: 36px; font-weight: 700; margin-bottom: 8px;">{neutral_pct:.1f}%</div>
                <div style="font-size: 16px; color: #888;">{"$" + format_number(neutral_amount) if show_amounts else "No amount data"}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
            <div class="kpi-box kpi-brown">
                <div style="font-size: 14px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">BROWN</div>
                <div class="kpi-value kpi-value-brown" style="font-size: 36px; font-weight: 700; margin-bottom: 8px;">{brown_pct:.1f}%</div>
                <div style="font-size: 16px; color: #888;">{"$" + format_number(brown_amount) if show_amounts else "No amount data"}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            f"""
            <div class="kpi-box">
                <div style="font-size: 14px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">TOTAL</div>
                <div style="font-size: 36px; font-weight: 700; margin-bottom: 8px; color: #2c3e50;">{format_number(total_amount) if show_amounts else "N/A"}</div>
                <div style="font-size: 16px; color: #888;">{"100%" if show_amounts else "No amount data"}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    # Primary visualization
    st.markdown(
        '<h2 style="color: #2c3e50;">Budget Composition</h2>', unsafe_allow_html=True
    )
    fig = plot_budget_donut(df)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Ministries tables
    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            '<div class="ministry-table"><h2 style="color: #2c3e50;">🟢 Top 5 Green Ministries</h2>',
            unsafe_allow_html=True,
        )
        top_green = get_top_ministries(df, "GREEN", 5)
        if len(top_green) > 0:
            display_df = top_green[["ministry", "amount_fmt", "pct"]].copy()
            display_df.columns = ["Ministry", "Amount", "% of Green"]
            display_df.index = range(1, len(display_df) + 1)
            st.table(display_df)
        else:
            st.info("No green spending data available")
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown(
            '<div class="ministry-table"><h2 style="color: #2c3e50;">🟤 Top 5 Brown Ministries</h2>',
            unsafe_allow_html=True,
        )
        top_brown = get_top_ministries(df, "BROWN", 5)
        if len(top_brown) > 0:
            display_df = top_brown[["ministry", "amount_fmt", "pct"]].copy()
            display_df.columns = ["Ministry", "Amount", "% of Brown"]
            display_df.index = range(1, len(display_df) + 1)
            st.table(display_df)
        else:
            st.info("No brown spending data available")
        st.markdown("</div>", unsafe_allow_html=True)

    # Strategic recommendation
    green_leader = top_green.iloc[0]["ministry"] if len(top_green) > 0 else "N/A"
    green_leader_pct = top_green.iloc[0]["pct"] if len(top_green) > 0 else 0
    brown_leader = top_brown.iloc[0]["ministry"] if len(top_brown) > 0 else "N/A"
    brown_leader_pct = top_brown.iloc[0]["pct"] if len(top_brown) > 0 else 0

    st.markdown(
        f"""
        <div class="recommendation-box">
            <div style="font-size: 18px; font-weight: 600; color: #e65100; margin-bottom: 10px;">💡 Strategic Recommendation</div>
            <div style="font-size: 16px; color: #5d4037; line-height: 1.6;">
                Prioritize green investment in <strong>{green_leader}</strong> ({green_leader_pct:.1f}% of all green spending) and address brown allocations in <strong>{brown_leader}</strong> ({brown_leader_pct:.1f}% of all brown spending).
            </div>
        </div>
    """,
        unsafe_allow_html=True,
    )


def show_analyst_dashboard():
    """Display full-featured analyst dashboard."""

    logger.info("Rendering analyst dashboard")

    # Header with navigation
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("📊 Analyst Dashboard")
    with col2:
        if st.button("🏠 Home", use_container_width=True):
            st.session_state.current_view = "landing"
            st.rerun()

    # Sidebar configuration
    with st.sidebar:
        st.header("🏢 Project Context")
        
        db_country = st.text_input(
            "Country / Entity", 
            value=st.session_state.db_country,
            help="Define the jurisdiction for this budget analysis"
        )
        st.session_state.db_country = db_country
        db_year = st.number_input(
            "Fiscal Year",
            min_value=2000,
            max_value=2100,
            value=st.session_state.db_year,
            help="Define the fiscal year for this budget analysis"
        )
        st.session_state.db_year = db_year

        admin_level = st.selectbox(
            "Administrative Level",
            options=["National", "Subnational", "Both"],
            index=0,
            help="Specify if this analysis covers national or subnational budgets"
        )
        
        budget_stage = st.selectbox(
            "Budget Stage",
            options=["Proposal", "Execution", "Both"],
            index=1,
            help="Specify the stage of the budget"
        )
        
        st.divider()
        st.header("⚙️ Tagging Configuration")

        # Tagging method selection
        tagging_method = st.radio(
            "Tagging Method",
            options=["Rio Markers", "EU Taxonomy"],
            help="Choose between Rio Convention markers or the 6 EU Taxonomy environmental objectives. Both use COFOG pre-screening.",
            key="tagging_method",
        )

        use_llm = st.checkbox(
            "Use AI-Powered Verification (LLM)",
            value=False,
            help="Enable Claude/GPT-4 for detailed analysis and higher accuracy",
        )

        analysis_depth = "Balanced"
        if use_llm:
            analysis_depth = st.select_slider(
                "Depth of Analysis",
                options=["Balanced", "Deep"],
                value="Balanced",
                help="Balanced: AI tags ambiguous items only (fast/efficient). Deep: AI reviews every line item (maximum accuracy, slower)."
            )

        llm_provider = None
        api_key = None

        if use_llm:
            llm_provider = st.selectbox(
                "LLM Provider",
                ["anthropic", "openai"],
                help="Choose between Claude (Anthropic) or GPT-4 (OpenAI)",
            )

            api_key_input = st.text_input(
                f"{llm_provider.upper()} API Key",
                type="password",
                help=f"Your {llm_provider} API key (not stored)",
            )

            if not api_key_input:
                if llm_provider == "anthropic":
                    api_key = os.environ.get("ANTHROPIC_API_KEY")
                else:
                    api_key = os.environ.get("OPENAI_API_KEY")

                if api_key:
                    st.info(f"✓ Using {llm_provider.upper()} API key from environment")
                else:
                    st.warning(f"⚠️ No {llm_provider.upper()} API key found")
            else:
                api_key = api_key_input

        st.divider()

        # Update description based on selected method
        if tagging_method == "COFOG Rules":
            st.markdown("### 📊 How it works")
            st.markdown("""
            1. **Phase 0 - COFOG Rules**: Uses government classification codes
            2. **Phase 1 - LLM (optional)**: Analyzes ambiguous cases in detail
            3. **Output**: Excel file with climate tags and confidence scores
            """)
        elif tagging_method == "Rio Markers":
            st.markdown("### 📊 How it works")
            st.markdown("""
            1. **Rio Marker System**: Uses Rio Convention/Paris Agreement framework
            2. **Marker Values**:
               - 0 = Not targeted (no climate relevance)
               - 1 = Significant objective (climate is one of several objectives)
               - 2 = Principal objective (climate is main purpose)
            3. **Split Tracking**: Separate mitigation and adaptation markers
            4. **Output**: Excel file with Rio Markers (0, 1, 2) and climate tags
            """)
        else:  # EU Taxonomy
            st.markdown("### 📊 How it works")
            st.markdown("""
            1. **EU Taxonomy**: Uses the 6 official EU environmental objectives
            2. **Objectives tracked**:
               - Mitigation & Adaptation
               - Water & Marine Resources
               - Circular Economy
               - Pollution & Biodiversity
            3. **Output**: Detailed mapping across all 6 objectives
            """)

        st.divider()
        st.markdown("### 📚 Methodology & Sources")
        st.markdown("""
        - **Rio Markers**: [OECD DAC Handbook](https://www.oecd.org/en/publications/oecd-dac-rio-markers-for-climate-handbook_d13200a9-en.html)
        - **EU Taxonomy**: [Regulation (EU) 2020/852](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32020R0852)
        - **COFOG**: [OECD GFS Guidelines](https://www.oecd.org/en/data/indicators/general-government-spending-by-function.html)
        """)

    # Custom CSS for nicer navigation tabs
    st.markdown("""
        <style>
        .stRadio [role="radiogroup"] {
            background-color: #f8f9fa;
            padding: 5px;
            border-radius: 10px;
            border: 1px solid #dee2e6;
            margin-bottom: 20px;
        }
        .stRadio [role="radiogroup"] label {
            background-color: transparent;
            padding: 8px 16px;
            border-radius: 7px;
            margin-right: 5px;
            transition: all 0.3s;
        }
        .stRadio [role="radiogroup"] label:hover {
            background-color: #e9ecef;
        }
        </style>
    """, unsafe_allow_html=True)
    
    analyst_tabs = ["📤 Upload & Tag", "📊 Results", "📋 Sample Data"]
    
    # Use a radio button styled to look like tabs
    active_tab = st.radio(
        "Analyst Navigation",
        options=analyst_tabs,
        horizontal=True,
        label_visibility="collapsed",
        index=analyst_tabs.index(st.session_state.analyst_tab),
        key="analyst_navigation_radio"
    )
    st.session_state.analyst_tab = active_tab

    if active_tab == "📤 Upload & Tag":
        st.header("Upload Budget File")

        col1, col2 = st.columns([2, 1])

        with col1:
            uploaded_file = st.file_uploader(
                "Choose a budget file (Excel or Word)",
                type=["xlsx", "xls", "docx"],
                help="File should contain COFOG classifications (func1-4, econ1-4, prog1-2)",
            )

        with col2:
            st.metric("Supported Formats", "Excel / Word")

        if uploaded_file is not None:
            logger.info("User uploaded file '%s' for tagging", uploaded_file.name)
            st.success(f"✓ File uploaded: {uploaded_file.name}")

            try:
                # temp_path = f"/tmp/{uploaded_file.name}"
                temp_dir = tempfile.gettempdir() 

                # 2. Combine the temp folder path with your file name safely
                temp_path = os.path.join(temp_dir, uploaded_file.name)
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())

                if uploaded_file.name.endswith(".docx"):
                    processor = WordProcessor(temp_path, api_key=api_key)
                    df_preview = processor.to_dataframe()
                    sheets = ["Extracted Content"]
                    active_sheet = "Extracted Content"
                else:
                    processor = ExcelProcessor(temp_path)
                    # Use processor.load() to trigger auto-detection and find the correct sheet (e.g., 'Raw')
                    df_preview = processor.load()
                    sheets = processor.get_sheet_names()
                    active_sheet = processor.sheet_name

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Sheets", len(sheets))
                with col2:
                    st.metric("Rows", f"{len(df_preview):,}")
                with col3:
                    st.metric("Columns", len(df_preview.columns))

                # Initialize or update mapping in session state
                if not st.session_state.column_mapping or st.session_state.get("last_uploaded_file") != uploaded_file.name:
                    st.session_state.column_mapping = processor.column_mapping
                    st.session_state.last_uploaded_file = uploaded_file.name
                    logger.info("Initialized column mapping for uploaded file '%s'", uploaded_file.name)

                with st.expander("🛠️ Manual Column Mapping", expanded=False):
                    st.info("💡 Map your Excel columns to the standard fields. Mandatory fields are marked with `*`.")
                    
                    # Define field groups for UI
                    field_groups = {
                        "🏛️ Functional Classification": ["func1", "func2", "func3", "func4"],
                        "💰 Economic Classification": ["econ1", "econ2", "econ3", "econ4"],
                        "📊 Dashboard & Context": ["amount", "admin1", "year", "prog1", "prog2"]
                    }
                    
                    field_labels = {
                        "func1": "Functional Level 1 (COFOG)",
                        "func2": "Functional Level 2",
                        "func3": "Functional Level 3",
                        "func4": "Functional Level 4",
                        "econ1": "Economic Level 1",
                        "econ2": "Economic Level 2",
                        "econ3": "Economic Level 3",
                        "econ4": "Economic Level 4",
                        "amount": "Budget Amount/Value *",
                        "admin1": "Ministry / Admin Level 1",
                        "year": "Fiscal Year / Period *",
                        "prog1": "Program Name / Description",
                        "prog2": "Sub-Program / Activity"
                    }

                    # Available columns from the uploaded file
                    NONE_OPTION = "None (Skip)"
                    available_columns = [NONE_OPTION] + list(df_preview.columns)
                    
                    # Interface with 3 columns for grouping
                    map_col1, map_col2, map_col3 = st.columns(3)
                    
                    with map_col1:
                        st.markdown("**Functional**")
                        for field in field_groups["🏛️ Functional Classification"]:
                            current_val = st.session_state.column_mapping.get(field, NONE_OPTION)
                            if current_val not in available_columns: current_val = NONE_OPTION
                            
                            mapped_col = st.selectbox(
                                field_labels[field],
                                options=available_columns,
                                index=available_columns.index(current_val),
                                key=f"map_{field}"
                            )
                            # Update mapping: Use None to indicate explicit skipping
                            st.session_state.column_mapping[field] = mapped_col if mapped_col != NONE_OPTION else None

                    with map_col2:
                        st.markdown("**Economic**")
                        for field in field_groups["💰 Economic Classification"]:
                            current_val = st.session_state.column_mapping.get(field, NONE_OPTION)
                            if current_val not in available_columns: current_val = NONE_OPTION
                            
                            mapped_col = st.selectbox(
                                field_labels[field],
                                options=available_columns,
                                index=available_columns.index(current_val),
                                key=f"map_{field}"
                            )
                            # Update mapping: Use None to indicate explicit skipping
                            st.session_state.column_mapping[field] = mapped_col if mapped_col != NONE_OPTION else None

                    with map_col3:
                        st.markdown("**Dashboard & Context**")
                        for field in field_groups["📊 Dashboard & Context"]:
                            current_val = st.session_state.column_mapping.get(field, NONE_OPTION)
                            if current_val not in available_columns: current_val = NONE_OPTION
                            
                            mapped_col = st.selectbox(
                                field_labels[field],
                                options=available_columns,
                                index=available_columns.index(current_val),
                                key=f"map_{field}"
                            )
                            # Update mapping: Use None to indicate explicit skipping
                            st.session_state.column_mapping[field] = mapped_col if mapped_col != NONE_OPTION else None

                st.divider()

                st.divider()
                st.subheader("Processing Options")

                col1, col2 = st.columns(2)
                with col1:
                    sample_size = st.number_input(
                        "Process first N rows (0 = all)",
                        min_value=0,
                        value=0,
                        step=100,
                        help="Useful for testing on sample before full dataset",
                    )

                with col2:
                    show_details = st.checkbox(
                        "Show detailed results",
                        value=True,
                        help="Display rationale and confidence for each classification",
                    )

                if st.button(
                    "🚀 Start Tagging", type="primary", use_container_width=True
                ):
                    logger.info(
                        "Starting tagging workflow for '%s' with method '%s' and sample size %s",
                        uploaded_file.name,
                        tagging_method,
                        sample_size,
                    )
                    with st.spinner("Processing... This may take a few minutes"):
                        try:
                            processor = ExcelProcessor(temp_path)
                            df = processor.load()
                            column_mapping = processor.column_mapping

                            if sample_size > 0:
                                df = df.head(sample_size)
                                st.info(f"Processing first {sample_size} rows")

                            mapping_path = os.path.join(os.path.dirname(__file__), "shared/cofog_mapping.json")

                            # NEW: Choose tagger based on selected method
                            if tagging_method == "Rio Markers":
                                # Use Rio Marker tagger
                                st.info("💡 Using Rio Convention Marker tagging...")

                                rio_tagger = RioMarkerTagger(
                                    use_llm=use_llm and bool(api_key),
                                    api_key=api_key,
                                    llm_provider=llm_provider or "anthropic",
                                    llm_for_ambiguous_only=(analysis_depth == "Balanced"),
                                    mapping_path=mapping_path,
                                )
                                rio_results = rio_tagger.tag_batch(df, st.session_state.column_mapping)

                                # Convert RioMarkerResult list to DataFrame
                                results_only_df = pd.DataFrame(
                                    {
                                        "climate_tag": [
                                            r.tagging_result.climate_tag.value
                                            for r in rio_results
                                        ],
                                        "confidence": [
                                            r.tagging_result.confidence.value
                                            for r in rio_results
                                        ],
                                        "method": [
                                            r.tagging_result.method for r in rio_results
                                        ],
                                        "environmental_objective": [
                                            r.tagging_result.environmental_objective.value
                                            for r in rio_results
                                        ],
                                        "subcategory": [
                                            r.tagging_result.subcategory or ""
                                            for r in rio_results
                                        ],
                                        "rationale": [
                                            r.tagging_result.rationale
                                            for r in rio_results
                                        ],
                                        "rio_marker": [
                                            r.rio_marker for r in rio_results
                                        ],
                                        "rio_mitigation": [
                                            r.rio_mitigation for r in rio_results
                                        ],
                                        "rio_adaptation": [
                                            r.rio_adaptation for r in rio_results
                                        ],
                                    }
                                )

                                # Reorder: Original columns first, then results
                                results_df = pd.concat([df.reset_index(drop=True), results_only_df], axis=1)

                            elif tagging_method == "EU Taxonomy":
                                # Use EU Taxonomy tagger
                                st.info("💡 Using EU Taxonomy Environmental Objectives tagging...")

                                eu_tagger = EUTaxonomyTagger(
                                    use_llm=use_llm and bool(api_key),
                                    api_key=api_key,
                                    llm_provider=llm_provider or "anthropic",
                                    llm_for_ambiguous_only=(analysis_depth == "Balanced"),
                                    mapping_path=mapping_path,
                                )
                                eu_results = eu_tagger.tag_batch(df, st.session_state.column_mapping)

                                # Convert EUTaxonomyResult list to DataFrame
                                results_only_df = pd.DataFrame(
                                    {
                                        "climate_tag": [
                                            r.tagging_result.climate_tag.value
                                            for r in eu_results
                                        ],
                                        "confidence": [
                                            r.tagging_result.confidence.value
                                            for r in eu_results
                                        ],
                                        "method": [
                                            r.tagging_result.method for r in eu_results
                                        ],
                                        "environmental_objective": [
                                            r.tagging_result.environmental_objective.value
                                            for r in eu_results
                                        ],
                                        "subcategory": [
                                            r.tagging_result.subcategory or ""
                                            for r in eu_results
                                        ],
                                        "rationale": [
                                            r.tagging_result.rationale
                                            for r in eu_results
                                        ],
                                    }
                                )

                                # Add the 6 objective columns
                                for obj in ["mitigation", "adaptation", "water", "circular_economy", "pollution", "biodiversity"]:
                                    results_only_df[f"eu_{obj}"] = [r.eu_objectives[obj] for r in eu_results]

                                # Reorder: Original columns first, then results
                                results_df = pd.concat([df.reset_index(drop=True), results_only_df], axis=1)

                            else:
                                # Use existing COFOG tagger
                                tagger = GreenBudgetTaggerOptimized(
                                    use_llm=use_llm and bool(api_key),
                                    api_key=api_key,
                                    llm_provider=llm_provider or "anthropic",
                                    llm_model=None,
                                )

                                from green_budget_tagger_optimized import (
                                    DeduplicationHelper,
                                )

                                dedup_helper = DeduplicationHelper(st.session_state.column_mapping)
                                dedup_df, mapping, _ = dedup_helper.deduplicate(df)

                                reduction = len(df) - len(dedup_df)
                                reduction_pct = (
                                    100 * reduction / len(df) if len(df) > 0 else 0
                                )

                                col1, col2, col3 = st.columns(3)
                                with col1:
                                    st.metric("Original Rows", f"{len(df):,}")
                                with col2:
                                    st.metric("Unique Combos", f"{len(dedup_df):,}")
                                with col3:
                                    st.metric("Reduction", f"{reduction_pct:.0f}%")

                                st.success(
                                    f"✨ LLM calls reduced from {len(df):,} to ~{len(dedup_df):,} ({reduction_pct:.0f}% savings)!"
                                )

                                results_df = tagger.process_file(
                                    temp_path, 
                                    output_path=None,
                                    llm_for_ambiguous_only=(analysis_depth == "Balanced"),
                                    column_mapping_override=st.session_state.column_mapping
                                )

                            if "comments" not in results_df.columns:
                                results_df["comments"] = ""

                            st.session_state.results_df = results_df
                            st.session_state.results_with_comments = results_df.copy()
                            st.session_state.processing_complete = True
                            st.session_state.tagged_file = uploaded_file.name
                            logger.info(
                                "Completed tagging for '%s' with %d rows and method '%s'",
                                uploaded_file.name,
                                len(results_df),
                                tagging_method,
                            )
                            # Note: tagging_method is automatically managed by st.radio() with key="tagging_method"

                            # Check for overwrite
                            if result_exists(db_country, db_year, tagging_method):
                                logger.warning(
                                    "Result record already exists for %s (%s) using method '%s'",
                                    db_country,
                                    db_year,
                                    tagging_method,
                                )
                                st.warning(f"⚠️ Findings for **{db_country} ({db_year})** tagged with **{tagging_method}** already exist in the intelligence database.")
                                if not st.checkbox("Confirm: Overwrite existing record?", value=False):
                                    st.info("ℹ️ Processing paused. Please confirm overwrite if you wish to proceed.")
                                    return

                            st.success("✅ Tagging complete!")
                            
                            # Automatically redirect to results
                            st.session_state.analyst_tab = "📊 Results"
                            st.rerun()

                        except Exception as e:
                            logger.exception("Processing failed for uploaded file '%s'", uploaded_file.name)
                            st.error(f"❌ Error during processing: {str(e)}")

            except Exception as e:
                logger.exception("Failed to read uploaded file '%s'", uploaded_file.name)
                st.error(f"❌ Error reading file: {str(e)}")

    elif active_tab == "📊 Results":
        st.header("📊 Results & Analysis")

        if (
            st.session_state.processing_complete
            and st.session_state.results_df is not None
        ):
            results_df = st.session_state.results_df

            # Summary metrics
            st.subheader("Summary Statistics")

            col1, col2, col3, col4 = st.columns(4)

            total_count = len(results_df)
            green_count = (results_df["climate_tag"] == "GREEN").sum()
            neutral_count = (results_df["climate_tag"] == "NEUTRAL").sum()
            brown_count = (results_df["climate_tag"] == "BROWN").sum()

            total_amount = calculate_total_amount(results_df, st.session_state.column_mapping)
            green_amount = calculate_amount(results_df, "GREEN", st.session_state.column_mapping)
            neutral_amount = calculate_amount(results_df, "NEUTRAL", st.session_state.column_mapping)
            brown_amount = calculate_amount(results_df, "BROWN", st.session_state.column_mapping)

            with col1:
                st.metric(
                    "🟢 GREEN", 
                    f"{format_number(green_amount)}", 
                    f"{green_count:,} items ({green_count / total_count * 100:.1f}%)"
                )
            with col2:
                st.metric(
                    "⚪ NEUTRAL",
                    f"{format_number(neutral_amount)}",
                    f"{neutral_count:,} items ({neutral_count / total_count * 100:.1f}%)",
                )
            with col3:
                st.metric(
                    "🟤 BROWN", 
                    f"{format_number(brown_amount)}", 
                    f"{brown_count:,} items ({brown_count / total_count * 100:.1f}%)"
                )
            with col4:
                st.metric(
                    "Total Budget", 
                    f"{format_number(total_amount)}",
                    f"{total_count:,} items"
                )

            # Confidence distribution
            st.subheader("Confidence Distribution")

            conf_dist = get_confidence_distribution(results_df)
            
            # Helper to calculate amount by confidence
            from shared.metrics import _get_amount_column
            amount_col = _get_amount_column(results_df, st.session_state.column_mapping)
            def get_conf_amount(conf):
                if amount_col and amount_col in results_df.columns:
                    return results_df[results_df["confidence"] == conf][amount_col].sum()
                return 0.0

            high_amt = get_conf_amount("HIGH")
            med_amt = get_conf_amount("MEDIUM")
            low_amt = get_conf_amount("LOW")

            col1, col2, col3 = st.columns(3)
            col1.metric(
                "HIGH",
                f"{format_number(high_amt)}",
                f"{conf_dist['HIGH']:,} items",
            )
            col2.metric(
                "MEDIUM",
                f"{format_number(med_amt)}",
                f"{conf_dist['MEDIUM']:,} items",
            )
            col3.metric(
                "LOW", 
                f"{format_number(low_amt)}", 
                f"{conf_dist['LOW']:,} items"
            )

            # Visualizations
            st.subheader("Budget Composition")
            st.plotly_chart(plot_budget_donut(results_df, st.session_state.column_mapping), use_container_width=True)
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Environmental Objectives (GREEN items)")
                objectives = get_green_objectives_distribution(results_df)
                if objectives:
                    for obj, count in objectives.items():
                        if obj and obj != "none":
                            st.text(f"{obj}: {count}")
                    fig = plot_green_objectives(results_df)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No environmental objectives data available")

            with col2:
                st.subheader("Tagging Methods Used")
                methods = get_method_distribution(results_df)
                if methods:
                    for method, count in methods.items():
                        st.text(f"{method}: {count}")
                    fig = plot_methods(results_df)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No method data available")

            # NEW: EU Taxonomy specific visualizations
            if tagging_method == "EU Taxonomy" or "eu_mitigation" in results_df.columns:
                st.divider()
                st.subheader("EU Taxonomy Analysis")
                
                col1, col2 = st.columns(2)
                with col1:
                    fig = plot_eu_taxonomy_objectives(results_df, st.session_state.column_mapping)
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    dist = get_eu_taxonomy_distribution(results_df)
                    st.write("**Top Ministries for Climate Mitigation (EU)**")
                    top_mit = get_eu_top_ministries(results_df, "mitigation", 3, st.session_state.column_mapping)
                    if not top_mit.empty:
                        st.table(top_mit[["ministry", "amount_fmt", "pct"]])
                    else:
                        st.info("No data for Mitigation")

            # NEW: Word Report Generation
            st.divider()
            st.subheader("📝 Export Summary Report")
            if st.button("📄 Generate Word Summary Report", use_container_width=True):
                try:
                    
                    # Prepare metadata
                    metadata = {
                        "country": st.session_state.db_country,
                        "year": st.session_state.db_year,
                        "admin_level": admin_level,
                        "budget_stage": budget_stage,
                        "method": tagging_method,
                        "currency": "USD" # Could be made dynamic
                    }
                    
                    # Prepare metrics
                    metrics = {
                        "green_pct": calculate_percentage(results_df, "GREEN", st.session_state.column_mapping),
                        "green_amount": calculate_amount(results_df, "GREEN", st.session_state.column_mapping),
                        "total_amount": calculate_total_amount(results_df, st.session_state.column_mapping),
                        "amount_col": _get_amount_column(results_df, st.session_state.column_mapping),
                        "sectoral_breakdown": get_sectoral_breakdown(results_df, mapping=st.session_state.column_mapping),
                        "objective_split": get_objective_split(results_df, mapping=st.session_state.column_mapping),
                        "trend_pp": calculate_trend_simple(results_df, mapping=st.session_state.column_mapping),
                        "capital_share": get_capital_share(results_df, mapping=st.session_state.column_mapping),
                        "confidence_dist": get_confidence_distribution(results_df)
                    }
                    
                    logger.info("Generating Word summary report for %s (%s)", st.session_state.db_country, st.session_state.db_year)
                    report_gen = ReportGenerator()
                    report_path = report_gen.generate_summary_report(
                        results_df,
                        metadata,
                        metrics
                    )
                    
                    with open(report_path, "rb") as f:
                        st.download_button(
                            label="⬇️ Download Word Report",
                            data=f,
                            file_name=report_path.name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            use_container_width=True
                        )
                    st.success(f"✓ Report generated: {report_path.name}")
                except Exception as e:
                    logger.exception("Failed to generate Word summary report")
                    st.error(f"Failed to generate report: {e}")

            # NEW: Database Commitment
            st.divider()
            st.subheader("🗄️ Database Management")
            col1, col2 = st.columns([2, 1])
            with col1:
                st.info(f"Finalize and commit findings for **{st.session_state.db_country} ({st.session_state.db_year})** to the platform database.")
            with col2:
                if st.button("💾 Commit to Intelligence Database", type="primary", use_container_width=True):
                    logger.info(
                        "Committing results to intelligence database for %s (%s) using method '%s'",
                        st.session_state.db_country,
                        st.session_state.db_year,
                        tagging_method,
                    )
                    file_path = save_result(
                        results_df, 
                        st.session_state.db_country, 
                        st.session_state.db_year, 
                        tagging_method
                    )
                    logger.info("Committed results to database file '%s'", os.path.basename(file_path))
                    st.success(f"📦 Successfully committed to Database: `{os.path.basename(file_path)}`")

            # Download results
            st.subheader("Download Results")

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df_to_export = (
                    st.session_state.results_with_comments
                    if st.session_state.results_with_comments is not None
                    else results_df
                )
                df_to_export.to_excel(writer, sheet_name="Tagged_Data", index=False)

                summary_data = {
                    "Metric": [
                        "Total Budget Lines",
                        "GREEN Count",
                        "GREEN %",
                        "NEUTRAL Count",
                        "NEUTRAL %",
                        "BROWN Count",
                        "BROWN %",
                        "HIGH Confidence",
                        "MEDIUM Confidence",
                        "LOW Confidence",
                    ],
                    "Value": [
                        len(results_df),
                        green_count,
                        f"{green_count / len(results_df) * 100:.1f}%",
                        neutral_count,
                        f"{neutral_count / len(results_df) * 100:.1f}%",
                        brown_count,
                        f"{brown_count / len(results_df) * 100:.1f}%",
                        conf_dist["HIGH"],
                        conf_dist["MEDIUM"],
                        conf_dist["LOW"],
                    ],
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

            output.seek(0)

            col1, col2 = st.columns([1, 1])

            with col1:
                st.download_button(
                    label="⬇️ Download Tagged Excel",
                    data=output.getvalue(),
                    file_name=f"tagged_{st.session_state.tagged_file}",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

            # Tag override section (NEW)
            st.subheader("Override Tags")
            st.info(
                "💡 Manually override automated tags before editing. Select rows to modify, choose override tag, and apply."
            )

            override_options = ["Keep Original", "GREEN", "NEUTRAL", "BROWN"]

            # Check if rio_marker column exists
            has_rio_markers = "rio_marker" in results_df.columns

            if has_rio_markers:
                col1, col2, col3 = st.columns(3)
                with col1:
                    override_tag = st.selectbox(
                        "Override climate_tag to:",
                        options=override_options,
                        key="override_tag_selector",
                    )
                with col2:
                    override_rio = st.selectbox(
                        "Override rio_marker to:",
                        options=[0, 1, 2],
                        format_func=lambda x: f"{x} - {get_rio_marker_label(x)}",
                        key="override_rio_selector",
                    )
                with col3:
                    override_reason = st.text_input(
                        "Override reason (optional):",
                        key="override_reason",
                        placeholder="e.g., Manual review, Policy change",
                    )
            else:
                override_tag = st.selectbox(
                    "Override climate_tag to:",
                    options=override_options,
                    key="override_tag_selector_no_rio",
                )
                override_reason = st.text_input(
                    "Override reason (optional):",
                    key="override_reason_no_rio",
                    placeholder="e.g., Manual review, Policy change",
                )

            if st.button("✏️ Apply Override to Selected Rows", key="apply_override_btn"):
                logger.info(
                    "User requested override: climate_tag=%s, rio_marker=%s",
                    override_tag,
                    override_rio if has_rio_markers else None,
                )
                st.success(
                    "✅ Override applied! Please select rows in the table below to apply tag changes."
                )
                st.info("💡 To apply overrides:")
                if has_rio_markers:
                    st.info(
                        f"- Select rows you want to modify\n- The selected rows will be tagged as: {override_tag} (climate_tag)\n- Rio marker: {override_rio}"
                    )
                else:
                    st.info(
                        f"- Select rows you want to modify\n- The selected rows will be tagged as: {override_tag} (climate_tag)"
                    )

            st.divider()

            # Show detailed results table
            st.subheader("Detailed Results")

            if "comments" not in results_df.columns:
                results_df["comments"] = ""
                st.session_state.results_df = results_df
                if st.session_state.results_with_comments is None:
                    st.session_state.results_with_comments = results_df.copy()

            col1, col2, col3 = st.columns(3)

            with col1:
                tag_filter = st.multiselect(
                    "Filter by climate tag",
                    ["GREEN", "NEUTRAL", "BROWN"],
                    default=["GREEN", "NEUTRAL", "BROWN"],
                    key="tag_filter",
                )

            with col2:
                confidence_filter = st.multiselect(
                    "Filter by confidence",
                    ["HIGH", "MEDIUM", "LOW"],
                    default=["HIGH", "MEDIUM", "LOW"],
                    key="conf_filter",
                )

            with col3:
                method_filter = st.multiselect(
                    "Filter by method",
                    results_df["method"].unique().tolist()
                    if "method" in results_df.columns
                    else [],
                    default=results_df["method"].unique().tolist()
                    if "method" in results_df.columns
                    else [],
                    key="method_filter",
                )

            if st.session_state.results_with_comments is not None:
                editable_df = st.session_state.results_with_comments.copy()
            else:
                editable_df = results_df.copy()

            editable_df = filter_data(
                editable_df,
                tag_filter=tag_filter,
                confidence_filter=confidence_filter,
                method_filter=method_filter,
            )

            st.info(
                "💡 You can edit 'Comments' column below. Click 'Save Comments' to preserve your changes."
            )

            edited_df = st.data_editor(
                editable_df,
                use_container_width=True,
                height=400,
                num_rows="dynamic",
                column_config={
                    "climate_tag": st.column_config.TextColumn("Tag", width="small"),
                    "confidence": st.column_config.TextColumn(
                        "Confidence", width="small"
                    ),
                    "method": st.column_config.TextColumn("Method", width="small"),
                    "environmental_objective": st.column_config.TextColumn(
                        "Objective", width="medium"
                    ),
                    "rationale": st.column_config.TextColumn(
                        "Rationale", width="large"
                    ),
                    "comments": st.column_config.TextColumn("Comments", width="large"),
                },
                key="results_editor",
            )

            col1, col2 = st.columns(2)

            with col1:
                if st.button(
                    "💾 Save Comments", type="primary", use_container_width=True
                ):
                    st.session_state.results_with_comments = edited_df
                    logger.info("Saved %d edited rows with comments", len(edited_df))
                    st.success("✅ Comments saved successfully!")
                    st.rerun()

            with col2:
                output_with_comments = io.BytesIO()
                with pd.ExcelWriter(output_with_comments, engine="openpyxl") as writer:
                    edited_df.to_excel(writer, sheet_name="Tagged_Data", index=False)

                    summary_data = {
                        "Metric": [
                            "Total Budget Lines",
                            "GREEN Count",
                            "GREEN %",
                            "NEUTRAL Count",
                            "NEUTRAL %",
                            "BROWN Count",
                            "BROWN %",
                            "HIGH Confidence",
                            "MEDIUM Confidence",
                            "LOW Confidence",
                        ],
                        "Value": [
                            len(results_df),
                            green_count,
                            f"{green_count / len(results_df) * 100:.1f}%",
                            neutral_count,
                            f"{neutral_count / len(results_df) * 100:.1f}%",
                            brown_count,
                            f"{brown_count / len(results_df) * 100:.1f}%",
                            conf_dist["HIGH"],
                            conf_dist["MEDIUM"],
                            conf_dist["LOW"],
                        ],
                    }
                    summary_df = pd.DataFrame(summary_data)
                    summary_df.to_excel(writer, sheet_name="Summary", index=False)

                output_with_comments.seek(0)

                st.download_button(
                    label="⬇️ Download with Comments",
                    data=output_with_comments.getvalue(),
                    file_name=f"tagged_with_comments_{st.session_state.tagged_file}",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

            st.info(f"Showing {len(edited_df):,} of {len(results_df):,} rows")

        else:
            st.info("👆 Upload and process a file to see results here")

    elif active_tab == "📋 Sample Data":
        st.header("📋 Sample Budget Data")

        st.markdown("""
        ### Expected File Format

        Your Excel file should contain these columns (names can vary):

        **Functional Classification (COFOG)**:
        - `func1`, `func2`, `func3`, `func4`: Hierarchical functional codes

        **Economic Classification**:
        - `econ1`, `econ2`, `econ3`, `econ4`: Type of spending

        **Administrative/Program Information**:
        - `admin1`, `admin2`, `admin3`: Administrative units
        - `prog1`, `prog2`: Ministry/program names and descriptions

        ### COFOG Code Examples

        | Code | Description | Expected Tag |
        |------|-------------|--------------|
        | 0100 | General public services | NEUTRAL |
        | 0200 | Defense | NEUTRAL |
        | 0500 | Environmental protection | GREEN |
        | 0400 | Economic affairs | AMBIGUOUS |
        | 0600 | Housing & community | AMBIGUOUS |

        ### Example Classifications
        """)

        sample_data = {
            "Functional (func1)": [
                "0500 Environmental protection",
                "0400 Economic activity",
                "0900 Education",
                "0600 Housing and community",
                "0200 Defense",
            ],
            "Program Description": [
                "Solar panel installation for public buildings",
                "Renewable energy development project",
                "Teacher salaries and education materials",
                "Municipal building renovation",
                "Military equipment maintenance",
            ],
            "Expected Tag": [
                "🟢 GREEN",
                "🟢 GREEN",
                "⚪ NEUTRAL",
                "🟡 AMBIGUOUS",
                "⚪ NEUTRAL",
            ],
            "Method": ["COFOG", "LLM", "COFOG", "LLM", "COFOG"],
        }

        sample_df = pd.DataFrame(sample_data)
        st.table(sample_df)

        st.markdown("""
        ### Tagging Logic

        **Phase 0 - COFOG Rules** (60-70% of items):
        - Uses functional classification codes
        - Applies keyword detection in program descriptions
        - Fast and deterministic

        **Phase 1 - LLM Analysis** (30-40% of items):
        - For ambiguous cases, Claude/GPT-4 provides detailed analysis
        - Considers full context and nuances
        - Returns confidence scores and detailed rationales
        """)


# Main execution
if __name__ == "__main__":
    if st.session_state.current_view == "landing":
        show_landing_page()
    elif st.session_state.current_view == "executive":
        show_executive_dashboard()
    elif st.session_state.current_view == "analyst":
        show_analyst_dashboard()
    else:
        show_landing_page()
