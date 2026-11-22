"""Sanity-check your environment: keys present + GitHub token actually works."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.config import settings


def main() -> int:
    keys = {
        "GEMINI_API_KEY": settings.gemini_api_key,
        "GROQ_API_KEY": settings.groq_api_key,
        "DEEPSEEK_API_KEY": settings.deepseek_api_key,
        "GITHUB_TOKEN": settings.github_token,
    }
    ok = True
    for name, val in keys.items():
        status = "OK     " if val else "MISSING"
        print(f"[{status}] {name}")
        ok &= bool(val)

    if settings.github_token:
        r = httpx.get(
            "https://api.github.com/rate_limit",
            headers={"Authorization": f"Bearer {settings.github_token}"},
            timeout=15,
        )
        if r.status_code == 200:
            core = r.json()["resources"]["core"]
            print(f"[OK     ] GitHub token valid. Rate limit: {core['remaining']}/{core['limit']} per hour")
        else:
            print(f"[FAIL   ] GitHub token rejected (HTTP {r.status_code})")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
