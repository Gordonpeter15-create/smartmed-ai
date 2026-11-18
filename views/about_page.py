"""About page: shows docs/LIMITATIONS.md so the app and the documentation always say the same thing."""
from pathlib import Path

import streamlit as st

from database import dataset_info

DOCS = Path(__file__).resolve().parent.parent / "docs"

info = dataset_info()
st.caption(
    f"Current data: **{info.get('source', 'unknown')}** · generator version {info.get('generator_version', '?')} · "
    f"seed {info.get('seed', '?')} · {info.get('start_date', '?')} to {info.get('end_date', '?')}"
)

limitations = DOCS / "LIMITATIONS.md"
if limitations.exists():
    st.markdown(limitations.read_text(encoding="utf-8"))
else:
    st.error("docs/LIMITATIONS.md is missing.")

st.divider()
st.markdown(
    "More documentation in the `docs/` folder: **GUIDE.md** (how to run, reset, inspect), **DATA.md** (data and "
    "synthetic generation), **METHODS.md** (what each alert means), **EVALUATION.md** (latest evaluation report, "
    "regenerate with `python evaluate.py`)."
)
