"""
LLM Gateway - FastAPI routes.
Forwards a prompt to an LLM provider (Groq for now) and logs tokens, latency and status.
Phase 2 adds more providers, retries, a circuit breaker and fallback.
"""
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import config
from app.db.logger import RequestLog

app = FastAPI(title="LLM Gateway")

# One shared request log for the whole app (tests swap this for a temporary one)
request_log = RequestLog(config.DB_PATH)


class ChatRequest(BaseModel):
    prompt: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    if not config.GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is missing. Add it to your .env file.")

    start = time.perf_counter()
    payload = {"model": config.MODEL, "messages": [{"role": "user", "content": req.prompt}]}
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(config.GROQ_URL, json=payload, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        latency = (time.perf_counter() - start) * 1000
        request_log.log("groq", config.MODEL, 0, 0, latency, "error")
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    data = resp.json()
    latency = (time.perf_counter() - start) * 1000
    usage = data.get("usage", {})
    request_log.log(
        "groq", config.MODEL, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), latency, "ok"
    )

    return {
        "answer": data["choices"][0]["message"]["content"],
        "latency_ms": round(latency, 1),
        "usage": usage,
    }


@app.get("/stats")
def stats() -> dict:
    return request_log.stats()
