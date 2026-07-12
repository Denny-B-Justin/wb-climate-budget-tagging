# Green Finance Intelligence Platform

Advanced institutional framework for automated budget alignment with Global Green Taxonomies and Rio Conventions. This platform transforms raw government expenditure data into actionable climate intelligence through a hybrid methodology combining rule-based heuristics and Large Language Model (LLM) analysis.

---

## 🏛️ System Overview

The platform is divided into two specialized environments tailored for different institutional roles:

### 1. Executive Monitoring Dashboard
Designed for decision-makers and senior management to track climate alignment across the entire portfolio.
- **Strategic KPI View**: Real-time breakdown of Green vs. Brown spending.
- **Ministry Leaderboards**: Identify which departments are driving climate objectives.
- **Historical Analysis**: Load verified findings from the Intelligence Database.
- **Trend Visualization**: Donut charts and objective distributions for quick reporting.

### 2. Policy Analyst Workspace
A high-fidelity environment for technical staff to process new budget datasets.
- **Flexible Tagging Frameworks**: Choose between COFOG, Rio Markers, or EU Taxonomy.
- **Hybrid AI Engine**: Use specialized prompts for Claude (Anthropic) or GPT-4 (OpenAI) to analyze ambiguous entries.
- **Manual Verification & Override**: Tools to explicitly correct or refine automated tags.
- **Database Management**: Commit finalized results to the permanent local database.
- **Smart Formatting**: Appends intelligence columns to the **right** of your original dataset, preserving the source structure.

---

## 🎯 Tagging Methodologies

### ✅ COFOG Rules (Institutional Standard)
Fast, deterministic classification based on the international *Classification of the Functions of Government*.
- **Administrative Scrubbing**: Automatically neutralizes salary and interest payments.
- **Keyword Verification**: Contextual checks for "Economic Affairs" and "Housing" sectors.

### ✅ Rio Convention Markers (OECD-DAC)
Identifies expenditures targeting the goals of the Rio Conventions.
- **Mitigation & Adaptation Markers**: Assigns scores of **0** (Not targeted), **1** (Significant), or **2** (Principal).
- **Policy Intent Support**: Detects alignment with NDCs and the Paris Agreement.

### ✅ EU Taxonomy Objectives (6 Pillars)
Screens every budget line against the technical screening criteria for:
1. Climate Change Mitigation
2. Climate Change Adaptation
3. Water & Marine Resources
4. Circular Economy transitions
5. Pollution Prevention & Control
6. Biodiversity & Ecosystems

---

## 📚 Keyword Library Samples

The platform's rule-based engine utilizes extensive, multi-sectoral keyword libraries to detect climate relevance.

### 📊 COFOG & Keyword Logic
Used to filter institutional sectors and detect specific signals in budget project text:
- **Green Signals**: `renewable`, `solar`, `wind`, `energy efficiency`, `public transport`, `reforestation`, `wetland`, `circular economy`, `led lighting`, `smart grid`.
- **Brown Signals**: `coal`, `oil`, `fossil fuel`, `natural gas`, `highway expansion`, `airport expansion`, `petroleum`, `coal mining`.
- **Exclusion Contexts**: `administrative`, `salary`, `pension`, `personnel costs`, `office supplies`, `debt service`, `interest payment`.

### 🌍 Rio Marker System (OECD-DAC)
Specialized for identifying policy intent and climate-vulnerability reduction:
- **Mitigation (Score 1/2)**: `low carbon`, `decarbonization`, `green hydrogen`, `battery storage`, `carbon capture`, `bioenergy`, `geothermal`.
- **Adaptation (Score 1/2)**: `flood control`, `drought resilience`, `climate resilient`, `early warning system`, `disaster risk reduction`, `irrigation`, `agricultural resilience`.
- **Explicit Intent**: `paris agreement`, `ndc`, `nationally determined contribution`, `climate action`, `ghg`, `carbon emission`.

### 🇪🇺 EU Taxonomy (6 Objectives)
Granular technical terms assigned to specific environmental pillars:
- **Circular Economy**: `recycling`, `waste management`, `refurbishment`, `resource efficiency`, `upcycling`, `zero waste`.
- **Pollution Control**: `air quality`, `emissions reduction`, `noise reduction`, `decontamination`, `soil remediation`, `hazardous waste`.
- **Water & Marine**: `wastewater`, `sewage`, `water quality`, `desalination`, `marine protection`, `aquatic ecosystem`, `river restoration`.

---

## 🚀 Getting Started

### 1. Installation

Clone the repository and install the required dependencies:

```bash
pip install -r requirements.txt
```

### 2. Set API Keys (Optional)

To enable the high-fidelity AI analysis, set your provider keys:

```bash
# For Anthropic (Claude)
export ANTHROPIC_API_KEY="your-key-here"

# For OpenAI (GPT-4)
export OPENAI_API_KEY="your-key-here"
```

### 3. Launching the Platform

Run the application using Streamlit:

```bash
streamlit run app.py
```

---

## 🛠️ Usage Workflow

### Step 1: Initialize Project Context (Analyst)
In the **Policy Analyst Workspace**, define the **Country** and **Fiscal Year**. These act as metadata for the Intelligence Database.

### Step 2: Upload & Process
1. Upload an Excel (.xlsx) file.
2. Select your **Tagging Method** (COFOG, Rio, or EU).
3. (Optional) Enable **"Use LLM"** for technical deep-dives on ambiguous items.
4. Click **Start Tagging**. The platform will automatically redirect you to the **Results** tab upon completion.

### Step 3: Verify & Commit
Review the findings, apply any manual **Overrides** if necessary, and click **💾 Commit to Intelligence Database**. This saves a processed record in `data/processed/` that can be accessed by the Executive Dashboard.

### Step 4: Executive Review
Switch to the **Executive Monitoring Dashboard**. Use the dropdown to browse previously committed findings, allowing for instant portfolio analysis without re-uploading data.

---

## 📂 File Structure

- `app.py`: Main entry point for the dual-dashboard platform.
- `green_budget_tagger.py`: Core logic for COFOG and Keyword analysis.
- `rio_marker_tagger.py`: Implementation of OECD-DAC Rio Marker logic.
- `eu_taxonomy_tagger.py`: Screening logic for the 6 EU Environmental Objectives.
- `shared/storage.py`: Data persistence layer (Local Intelligence Database).
- `data/processed/`: Permanent storage for locally committed results.

---

## ⚖️ License & Support

**World Bank Green Budget Tagging Initiative**
For institutional support, please refer to the internal documentation or contact the technical lead.
