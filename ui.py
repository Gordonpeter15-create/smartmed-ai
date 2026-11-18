"""Small helpers shared by the Streamlit pages: messages, pickers, cached results, and the visual style."""
from html import escape
from pathlib import Path
import re

import pandas as pd
import streamlit as st

# ---------- visual style ----------
ASSETS = Path(__file__).resolve().parent / "assets"
# One colour per meaning, used for banners, stat cards, and table badges.
TONES = {
    "teal": "#0D9488", "blue": "#2563EB", "indigo": "#4F46E5", "violet": "#7C3AED", "rose": "#E11D48",
    "red": "#DC2626", "orange": "#EA580C", "amber": "#D97706", "green": "#16A34A", "slate": "#64748B",
}
# Banner gradients (start, end) per page colour.
GRADIENTS = {
    "teal": ("#0F766E", "#14B8A6"), "blue": ("#1D4ED8", "#0EA5E9"), "indigo": ("#4338CA", "#8B5CF6"),
    "violet": ("#6D28D9", "#C026D3"), "rose": ("#BE123C", "#F97316"), "amber": ("#B45309", "#F59E0B"),
    "red": ("#B91C1C", "#F97316"), "slate": ("#334155", "#0D9488"),
}
# Soft badge colours for status words in tables: (background, text). Readable in light and dark mode.
BADGES = {
    "red": ("#FDE2E1", "#991B1B"), "orange": ("#FFEDD5", "#9A3412"), "amber": ("#FEF3C7", "#92400E"),
    "green": ("#DCFCE7", "#166534"), "blue": ("#DBEAFE", "#1E40AF"), "violet": ("#EDE9FE", "#5B21B6"),
    "teal": ("#CCFBF1", "#115E59"), "slate": ("#E2E8F0", "#334155"),
}

_CSS = """
<style>
.block-container { padding-top: 3.6rem; }
/* Page banner */
.sm-hero { background: linear-gradient(120deg, var(--c1), var(--c2)); color: #fff; padding: 1.25rem 1.5rem 1.15rem;
           border-radius: 1rem; margin: 0 0 0.6rem; box-shadow: 0 8px 24px -10px var(--c1); }
.sm-hero .sm-tag { display: inline-block; font-size: 0.72rem; letter-spacing: .04em; text-transform: uppercase;
                   background: rgba(255,255,255,.18); border: 1px solid rgba(255,255,255,.35);
                   padding: .12rem .6rem; border-radius: 999px; margin-bottom: .45rem; }
.sm-hero .sm-title { font-size: 2rem; font-weight: 800; line-height: 1.15; margin: 0; color: #fff; }
.sm-hero .sm-sub { margin: .35rem 0 0; opacity: .93; font-size: 1rem; }
@media (max-width: 640px) { .sm-hero .sm-title { font-size: 1.5rem; } .sm-hero { padding: 1rem 1.1rem; } }
/* Stat cards: containers with key "kpi-<tone>-..." */
[class*="st-key-kpi-"] { border-radius: .9rem; padding: .75rem .95rem .55rem; border: 1px solid transparent;
                         border-left-width: 5px; }
[class*="st-key-kpi-"] [data-testid="stMetricLabel"] p { font-weight: 600; }
/* Let card labels wrap instead of being cut off with "…" */
[class*="st-key-kpi-"] [data-testid="stMetricLabel"], [class*="st-key-kpi-"] [data-testid="stMetricLabel"] * {
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important; }
/* Section panels: containers with key "panel-<tone>-..." get a coloured left edge */
[class*="st-key-panel-"] { border-left-width: 5px !important; }
TONE_RULES
</style>
"""


def _tone_rules() -> str:
    rules = []
    for tone, color in TONES.items():
        rules.append(
            f'[class*="st-key-kpi-{tone}-"] {{ background: {color}17; border-color: {color}40; border-left-color: {color}; }}'
            f'[class*="st-key-kpi-{tone}-"] [data-testid="stMetricValue"] {{ color: {color}; }}'
            f'[class*="st-key-panel-{tone}-"] {{ border-left-color: {color} !important; }}'
        )
    return "\n".join(rules)


def apply_style() -> None:
    """Add the shared CSS and the sidebar logo. Called once per run from app.py."""
    st.markdown(_CSS.replace("TONE_RULES", _tone_rules()), unsafe_allow_html=True)
    st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"), size="large")


def page_header(icon: str, title: str, subtitle: str, tone: str = "teal") -> None:
    """A coloured banner at the top of a page."""
    start, end = GRADIENTS.get(tone, GRADIENTS["teal"])
    st.html(
        f'<div class="sm-hero" style="--c1:{start};--c2:{end}">'
        f'<div class="sm-tag">SmartMed AI · prototype · synthetic data</div>'
        f'<p class="sm-title">{escape(icon)} {escape(title)}</p>'
        f'<p class="sm-sub">{escape(subtitle)}</p></div>'
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def stat_card(label: str, value, tone: str = "teal", help: str | None = None, key: str | None = None) -> None:
    """A tinted card with one number. `tone` is a colour name from TONES."""
    with st.container(key=f"kpi-{tone}-{key or _slug(label)}"):
        st.metric(label, value, help=help)


def panel(tone: str, key: str):
    """A bordered section with a coloured left edge. Use as `with panel("amber", "stock"):`."""
    return st.container(border=True, key=f"panel-{tone}-{key}")


def badge_styles(frame: pd.DataFrame, colors: dict[str, dict[str, str]]):
    """Colour status words in table columns. `colors` = {column: {value: tone}}. Returns a pandas Styler."""
    def style_for(tone_by_value):
        def style(value):
            for prefix, tone in tone_by_value.items():
                if isinstance(value, str) and prefix in value:
                    background, text = BADGES[tone]
                    return f"background-color: {background}; color: {text}; font-weight: 600"
            return ""
        return style

    styler = frame.style
    for column, tone_by_value in colors.items():
        if column in frame.columns:
            styler = styler.map(style_for(tone_by_value), subset=[column])
    return styler


def form_key(name: str) -> str:
    """A form key that changes after each successful save, which empties the form.

    (Streamlit's clear_on_submit would also empty the form after an error,
    making the user type everything again.)
    """
    return f"{name}_{st.session_state.get(f'{name}_saves', 0)}"


def flash(message: str, clear_form: str | None = None) -> None:
    """Remember a success message, optionally empty a form, and reload the page with the new data."""
    st.session_state["flash"] = message
    if clear_form:
        st.session_state[f"{clear_form}_saves"] = st.session_state.get(f"{clear_form}_saves", 0) + 1
    st.rerun()


def show_flash() -> None:
    """Show the message saved by flash(), once."""
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)


def pick_medicine(medicines: list, label: str = "Medicine", key: str | None = None):
    """A dropdown of medicine rows that returns the chosen row (or None if the list is empty)."""
    if not medicines:
        st.info("No active medicines yet. Register one on the Medicines page.")
        return None
    by_id = {row["id"]: row for row in medicines}
    chosen = st.selectbox(label, list(by_id), format_func=lambda medicine_id: by_id[medicine_id]["name"], key=key)
    return by_id[chosen]


@st.cache_data(show_spinner="Running the forecasting pipeline…")
def _run_forecast(signature: tuple):
    # `signature` changes whenever the history changes, which tells Streamlit to re-run.
    import forecasting

    result = forecasting.run_forecasting()
    history, _ = forecasting.prepare_history(forecasting.load_demand_history())
    return result, history


def cached_forecast():
    """Return (ForecastResult, cleaned history), or (None, None) if there is no history.
    Shared by the Dashboard, Forecasting, and Stockout risk pages so it runs once."""
    import forecasting

    signature = forecasting.data_signature()
    if not signature[0]:
        return None, None
    return _run_forecast(signature)


@st.cache_data(show_spinner="Running the 4-period backtest…")
def _run_backtest(signature: tuple):
    import forecasting

    history, _ = forecasting.prepare_history(forecasting.load_demand_history())
    return forecasting.backtest(forecasting.build_features(history).dropna(subset=["target"]))


def cached_backtest():
    """Return the multi-period backtest table (re-run only when the history changes)."""
    import forecasting

    return _run_backtest(forecasting.data_signature())
