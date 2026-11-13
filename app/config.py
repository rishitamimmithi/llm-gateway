"""
All gateway settings live here, so nothing is hard-coded inside the logic.
Values come from the .env file (or real environment variables), with safe defaults.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env into environment variables

# --- Groq provider ---
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_URL: str = "https://api.groq.com/openai/v1/chat/completions"
MODEL: str = os.getenv("MODEL", "openai/gpt-oss-20b")  # check the Groq console for current model names

# --- Gemini provider ---
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
# Stable, free-tier "flash-lite" model; avoid gemini-2.5-flash (deprecated, rerouted from 2026-12-01)
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# --- Networking ---
REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))

# --- Database ---
DB_PATH: str = os.getenv("DB_PATH", "requests.db")
