"""Small helpers shared by the Streamlit pages."""
import streamlit as st


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
