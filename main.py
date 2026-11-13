"""
LLM Gateway - Step 1: a basic proxy.
Forwards a prompt to one LLM provider (Groq) and logs tokens, latency and status.
Later steps add fallback, rate limiting, caching and a dashboard.
"""
import os
import sqlite3
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()  # reads GROQ_API_KEY and MODEL from the .env file
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("MODEL", "llama-3.1-8b-instant")  # check the Groq console for current model names

app = FastAPI(title="LLM Gateway")

# A small database that records every request (we'll use this for cost and latency charts later)
db = sqlite3.connect("requests.db", check_same_thread=False)
db.execute(
    """CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, provider TEXT, model TEXT,
        prompt_tokens INTEGER, completion_tokens INTEGER,
        latency_ms REAL, status TEXT)"""
)
db.commit()


class ChatRequest(BaseModel):
    prompt: str


def log_request(provider, model, prompt_tokens, completion_tokens, latency_ms, status):
    db.execute(
        "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time.time(), provider, model, prompt_tokens, completion_tokens, latency_ms, status),
    )
    db.commit()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is missing. Add it to your .env file.")

    start = time.perf_counter()
    payload = {"model": MODEL, "messages": [{"role": "user", "content": req.prompt}]}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(GROQ_URL, json=payload, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        latency = (time.perf_counter() - start) * 1000
        log_request("groq", MODEL, 0, 0, latency, "error")
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    data = resp.json()
    latency = (time.perf_counter() - start) * 1000
    usage = data.get("usage", {})
    log_request("groq", MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), latency, "ok")

    return {
        "answer": data["choices"][0]["message"]["content"],
        "latency_ms": round(latency, 1),
        "usage": usage,
    }


@app.get("/stats")
def stats():
    row = db.execute(
        "SELECT COUNT(*), AVG(latency_ms), SUM(prompt_tokens + completion_tokens) "
        "FROM requests WHERE status = 'ok'"
    ).fetchone()
    return {
        "successful_requests": row[0],
        "avg_latency_ms": round(row[1] or 0, 1),
        "total_tokens": row[2] or 0,
    }
