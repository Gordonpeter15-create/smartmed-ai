"""SmartMed AI — start here: `streamlit run app.py`.

This file sets up the page, prepares the database, and shows the sidebar
navigation. Each screen lives in its own file inside the views/ folder.
"""
import streamlit as st

from database import initialize_database
from seed_data import seed_database

st.set_page_config(page_title="SmartMed AI", page_icon="💊", layout="wide")
initialize_database()  # create missing tables and upgrade older databases
seed_database()        # add synthetic sample data only if the database is empty

navigation = st.navigation(
    [
        st.Page("views/dashboard_page.py", title="Dashboard", icon="📊", default=True),
        st.Page("views/medicines_page.py", title="Medicines", icon="💊"),
        st.Page("views/inventory_page.py", title="Inventory", icon="📦"),
        st.Page("views/forecasting_page.py", title="Forecasting", icon="📈"),
        st.Page("views/stockout_page.py", title="Stockout risk", icon="⏳"),
        st.Page("views/reorder_page.py", title="Reorder suggestions", icon="🛒"),
        st.Page("views/expiry_page.py", title="Batch expiry", icon="🗓️"),
        st.Page("views/about_page.py", title="About & limitations", icon="ℹ️"),
    ]
)
st.sidebar.caption("Educational prototype · synthetic sample data · not for clinical or supply decisions.")
navigation.run()
