# GraphOne Intelligence Pipeline (trial submission)

Status: Step 0 scaffold. Full README written at the end.

## Setup
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    playwright install chromium
    cp .env.example .env   # fill in keys
    python scripts/check_env.py
    pytest
