# ── Visual identity for Specula (legacy: Le Visionnaire) ───────────────────────────────────────
# To revert to legacy theme, swap the values below with the LEGACY palette
# stored in memory/project_visionnaire_theme.md

# Backgrounds
BG          = "#080B14"
BG_CARD     = "#0F1320"
GRID        = "#161D2E"
BORDER      = "#1E2840"

# Typography
TEXT_BRIGHT = "#EEF0F6"
TEXT_MID    = "#9AA3B8"
TEXT_DIM    = "#4A5568"

# Accent & actions
ACCENT      = "#818CF8"    # indigo — primary, nav active, CTA
POSITIVE    = "#34D399"    # emerald green
NEGATIVE    = "#F87171"    # soft red
SWITCH      = "#60A5FA"    # sky blue
TRIM        = "#FBBF24"    # amber

# Chart-specific
PORTFOLIO_LINE  = ACCENT
BENCHMARK_LINE  = "#3A4560"
HLINE_COLOR     = "#252D40"

# Benchmark lines on the performance charts. Until 2026-10-09 every index was
# a shade of gray told apart by its dash pattern only, which visitors could not
# read. Each index keeps one color on every page (color follows the entity).
# The set was checked with the data-viz palette validator against the chart
# background (#080B14), all pairs, together with each portfolio's accent
# (Visionnaire #A78BFA, Bâtisseur #F5B60A, Nakamoto #FF6A00): it clears the
# colorblind separation and normal-vision floors on all three pages. Dash
# patterns stay as the second cue. Unknown labels fall back by role.
BENCHMARK_COLORS = {
    "S&P 500":              "#C3C2B7",   # light neutral
    "Nasdaq 100":           "#D95926",   # orange
    "S&P 500 Equal Weight": "#3987E5",   # blue
    "Bitcoin":              "#C3C2B7",
    "MSTR":                 "#3987E5",
}
BENCHMARK_FALLBACK = {"primary": "#C3C2B7", "secondary": "#D95926", "extra": "#3987E5"}

# Nav
NAV_ACTIVE_COLOR = ACCENT
NAV_ACTIVE_BG    = "rgba(129, 140, 248, 0.10)"

# Stacked area palette (positions, by ticker order)
POSITION_COLORS = [
    "#6366F1",   # indigo
    "#34D399",   # emerald
    "#60A5FA",   # sky
    "#F97316",   # orange
    "#FCD34D",   # yellow
    "#6B7280",   # gray
    "#93C5FD",   # light blue
    "#C084FC",   # purple
    "#FDBA74",   # pale orange
    "#FDE68A",   # pale yellow
    "#F472B6",   # pink
]

CASH_COLOR  = "#1A2235"

# ── Semantic color maps for donut charts ──────────────────────────────────────
# Sector
SECTOR_COLORS = {
    "Tech":          "#6366F1",   # strong indigo
    "Healthcare":    "#34D399",   # emerald
    "Finance":       "#F97316",   # bitcoin orange
    "Communication": "#60A5FA",   # sky blue
    "Industrials":   "#6B7280",   # neutral gray
    "Consumer":      "#FCD34D",   # yellow
    "Energy":        "#FB923C",   # warm orange
    "Materials":     "#A8A29E",   # stone
    "Real Estate":   "#818CF8",   # soft indigo
    "Utilities":     "#94A3B8",   # slate
    "Cash":              "#374151",   # dark gray
    "Cash/Equivalent":   "#374151",   # dark gray
}

# Geography
GEO_COLORS = {
    "USA":           "#6366F1",   # same as Tech — USA = dominant
    "Europe":           "#93C5FD",   # light blue
    "Japan":            "#FDBA74",   # pale orange
    "Asia ex-Japan":    "#FDE68A",   # pale yellow
    "Emerging Markets": "#FCD34D",   # yellow
    "LatAm":            "#86EFAC",   # light green
    "Global":           "#C084FC",   # purple
    "Other":            "#6B7280",   # gray
    "USD":              "#374151",   # same as Cash
}

# Thematic
THEMATIC_COLORS = {
    "AI / Semi":              "#6366F1",   # same as Tech
    "Crypto Currencies Play": "#F97316",   # bitcoin orange
    "Biotech":                "#059669",   # dark emerald
    "Space / Defense":        "#6B7280",   # gray
    "Consumer Growth":        "#FCA5A5",   # light red
    "Robotics / Automation":  "#C084FC",   # purple
    "Fintech / Payments":     "#60A5FA",   # sky blue
    "Energy Transition":      "#FCD34D",   # yellow
    "Software / SaaS":        "#818CF8",   # soft indigo
    "Cybersecurity":          "#F472B6",   # pink
    "Cloud / Infrastructure": "#1E40AF",   # dark blue
    "Clean Energy":           "#4ADE80",   # light green
    "Digital Health":         "#34D399",   # emerald (same family as Healthcare)
    "Energy Shortage":        "#FDE68A",   # pale yellow — broad energy theme
    "Communication":          "#93C5FD",   # light blue
    "Social Platform":        "#F472B6",   # pink
    "EdTech":                 "#A78BFA",   # violet
    "EV / China":             "#86EFAC",   # light green
    "Other":                  "#94A3B8",   # slate
    "Cash":                   "#374151",   # dark gray
    "Cash/Equivalent":        "#374151",   # dark gray
}


def action_colors():
    return {
        "IN":     POSITIVE,
        "OUT":    NEGATIVE,
        "SWITCH": SWITCH,
        "TRIM":   TRIM,
    }


def chart_layout(height=400):
    """Base Plotly layout for all charts."""
    return dict(
        plot_bgcolor=BG,
        paper_bgcolor=BG,
        font=dict(color=TEXT_MID, size=11),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        yaxis=dict(gridcolor=GRID, zeroline=False),
        xaxis=dict(gridcolor=GRID),
        hovermode="closest",
        height=height,
        margin=dict(l=0, r=0, t=30, b=0),
    )
