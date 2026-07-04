"""Run the localhost-only admin web dashboard.

Usage:
    pip install -r requirements.txt
    python dashboard.py

Then open http://127.0.0.1:8088 in your browser and log in with the
DASHBOARD_PASSWORD you set in .env.
"""
from src.admin_dashboard.server import main

if __name__ == "__main__":
    main()
