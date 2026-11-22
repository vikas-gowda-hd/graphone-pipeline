"""Central configuration. All tunables come from env vars so scaling needs no code changes."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    user_agent: str = os.getenv("USER_AGENT", "GraphOneTrialBot/0.1")
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "20"))
    per_domain_concurrency: int = int(os.getenv("PER_DOMAIN_CONCURRENCY", "3"))
    llm_max_input_chars: int = int(os.getenv("LLM_MAX_INPUT_CHARS", "12000"))


settings = Settings()
