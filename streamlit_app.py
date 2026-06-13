"""Shim: delegate streamlit app to src.streamlit_app."""
from src.streamlit_app import main as _main


if __name__ == "__main__":
    _main()
