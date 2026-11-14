"""
Locust load test: many simulated clients sending the realistic prompt mix (loadtest/prompt_mix.json).

Start the gateway with the fake provider and limits high enough not to get in the way, e.g.
    PROVIDER_ORDER=fake  TOKEN_BUCKET_CAPACITY=1000000  TOKEN_BUCKET_REFILL_PER_SECOND=1000000
    DB_PATH=loadtest.db  (fresh database per run)
then, with LOADTEST_API_KEY set to one of GATEWAY_API_KEYS:
    locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 50 -r 10 -t 60s --csv results

Each simulated user sends a request, waits 0-100 ms, and repeats. 429 and 5xx count as failures.
"""
import os
import random

from locust import FastHttpUser, between, events, task

from loadtest.savings_replay import build_mix

API_KEY = os.getenv("LOADTEST_API_KEY", "")
# A long seeded sequence of (group, prompt); each user starts at a random point in it
PROMPTS = [prompt for _, prompt in build_mix(n=5000, seed=7, zipf_s=1.0)]


@events.test_start.add_listener
def check_key(environment, **_) -> None:
    if not API_KEY:
        raise SystemExit("Set LOADTEST_API_KEY to one of the gateway's client keys")


class ChatUser(FastHttpUser):
    wait_time = between(0, 0.1)

    def on_start(self) -> None:
        self.position = random.randrange(len(PROMPTS))

    @task
    def chat(self) -> None:
        prompt = PROMPTS[self.position % len(PROMPTS)]
        self.position += 1
        with self.client.post(
            "/chat",
            json={"prompt": prompt},
            headers={"X-API-Key": API_KEY},
            name="/chat",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
