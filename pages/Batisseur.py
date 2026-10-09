import streamlit as st
from utils import SPECULA_ICON
from utils.portfolio import render_portfolio_page

st.set_page_config(
    page_title="Le Bâtisseur",
    page_icon=SPECULA_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_portfolio_page("batisseur", options={
    # Internal Layer taxonomy (Obvious / Haute Qualité / Diversification / Tactical)
    # is collapsed into the public IPS framing: Quality Compounders + Tactical.
    "show_donuts": ["Layer", "Sector", "Thematic", "Geography"],
    "layer_map": {
        "Obvious":         "Quality Compounders",
        "Haute Qualité":   "Quality Compounders",
        "Diversification": "Quality Compounders",
        # Tactical stays Tactical
    },
    # A 26-28 line book with no line above ~9% compares naturally with the
    # equal-weight index too (Sep 2026: S&P 500 -0.3%, equal weight -4.8%).
    # Hidden by default, one click in the legend.
    "extra_benchmarks": [("RSP", "S&P 500 Equal Weight")],
})
