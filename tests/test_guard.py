import numpy as np
import pytest

from app.cache.answer import CachedAnswer
from app.cache.guard import conflict, directions, numbers, tokens
from app.cache.semantic import SemanticCache
from tests.test_semantic_cache import FakeEmbedder


@pytest.mark.parametrize(
    "a, b, reason",
    [
        ("Convert 10 USD to INR", "Convert 100 USD to INR", "numbers"),
        ("Give me 3 tips", "Give me three tips", None),  # number word = digit
        ("Who was the first US president?", "Who was the third US president?", "numbers"),
        ("Explain the 1st law", "Explain the first law", None),
        ("Population in 1,000 towns", "Population in 1000 towns", None),
        ("Which foods are safe for dogs?", "Which foods are not safe for dogs?", "negation"),
        ("Why should I use it?", "Why shouldn't I use it?", "negation"),
        ("Which animals can swim?", "Which animals cannot swim?", "negation"),
        ("When should I use recursion?", "When should I avoid recursion?", "negation"),
        ("Convert 10 USD to INR", "Convert 10 INR to USD", "direction"),
        ("Copy files from my laptop to a server", "Copy files from a server to my laptop", "direction"),
        ("Is Python faster than Java?", "Is Java faster than Python?", "direction"),
        ("Translate 'hi' into French", "How do you say 'hi' in French?", None),  # only one has "into"
        ("How do I install numpy?", "What's the command to install numpy?", None),
        ("What is the capital of France?", "Which city is the capital of France?", None),
        ("", "", None),
        ("?!", "...", None),
        ("é 5", "é 5", None),
        ("explain gpt4 pricing", "explain gpt5 pricing", "numbers"),  # digits inside a word
        ("what is 1,5 plus 2", "what is 15 plus 2", "numbers"),  # not a thousands comma
        ("how many is 1,000", "how many is 1000", None),
        ("isnt it safe", "is it safe", "negation"),  # no apostrophe
        ("I dont like it", "I do not like it", None),
        ("how do I convert USD to INR", "how to convert USD to INR", None),  # "how to" isn't a direction
        ("I want to convert USD to INR", "convert USD to INR", None),
        ("how to convert USD to INR", "how to convert INR to USD", "direction"),
    ],
)
def test_conflict(a, b, reason):
    assert conflict(a, b) == reason
    assert conflict(b, a) == reason  # order of the two prompts doesn't matter


def test_helpers():
    words = tokens("Don't convert 5 miles to the km")
    assert words == ["do", "not", "convert", "5", "miles", "to", "the", "km"]
    assert numbers(words) == ["5"]
    assert directions(words) == {"to": ["km"]}
    assert directions(tokens("go to")) == {}  # nothing after "to"


EMBED = {"Convert 10 USD to INR": [1, 0, 0], "Convert 10 INR to USD": [0.99, 0.141, 0]}


def make(guard: bool) -> SemanticCache:
    return SemanticCache(FakeEmbedder(EMBED), threshold=0.9, max_entries=10, ttl_seconds=60, guard=guard)


async def store(cache, prompt, text):
    cache.add(await cache.embed(prompt), CachedAnswer(text, "groq", "m", 1, 1, 0.0), prompt)


@pytest.mark.asyncio
async def test_guard_blocks_look_alike_hit():
    cache = make(guard=True)
    await store(cache, "Convert 10 USD to INR", "830 INR")
    prompt = "Convert 10 INR to USD"
    assert cache.lookup(await cache.embed(prompt), prompt) is None
    assert cache.guard_blocks == 1


@pytest.mark.asyncio
async def test_guard_off_returns_the_wrong_answer():
    cache = make(guard=False)
    await store(cache, "Convert 10 USD to INR", "830 INR")
    prompt = "Convert 10 INR to USD"
    assert cache.lookup(await cache.embed(prompt), prompt).answer.text == "830 INR"


@pytest.mark.asyncio
async def test_next_best_match_is_used_when_best_is_blocked():
    cache = make(guard=True)
    await store(cache, "Convert 10 USD to INR", "830 INR")  # most similar, but wrong direction
    cache.add(np.array([0.95, 0.312, 0], dtype=np.float32), CachedAnswer("0.12 USD", "groq", "m", 1, 1, 0.0),
              "convert 10 INR to USD please")
    prompt = "Convert 10 INR to USD"
    hit = cache.lookup(await cache.embed(prompt), prompt)
    assert hit.answer.text == "0.12 USD"
    assert cache.guard_blocks == 1


def test_long_prompt_is_checked_in_linear_time():
    import time

    prompt = "to " * 50_000
    start = time.perf_counter()
    assert conflict(prompt, prompt) is None
    assert time.perf_counter() - start < 2  # was ~24 s when each "to" copied the rest of the list


@pytest.mark.asyncio
async def test_reused_slot_is_checked_against_the_new_prompt():
    from tests.test_lru_cache import FakeClock

    clock = FakeClock()
    cache = SemanticCache(FakeEmbedder(EMBED), threshold=0.9, max_entries=1, ttl_seconds=60, clock=clock)
    await store(cache, "Convert 10 USD to INR", "830 INR")
    clock.now += 61  # expired: the next add reuses the slot
    await store(cache, "Convert 10 INR to USD", "0.12 USD")
    prompt = "Convert 10 INR to USD"
    assert cache.lookup(await cache.embed(prompt), prompt).answer.text == "0.12 USD"
    assert cache.guard_blocks == 0


@pytest.mark.asyncio
async def test_all_candidates_blocked_is_a_miss():
    cache = make(guard=True)
    await store(cache, "Convert 10 USD to INR", "830 INR")
    cache.add(np.array([0.999, 0.04, 0], dtype=np.float32), CachedAnswer("x", "groq", "m", 1, 1, 0.0),
              "Convert 10 USD to INR now")
    prompt = "Convert 10 INR to USD"
    assert cache.lookup(await cache.embed(prompt), prompt) is None
    assert cache.guard_blocks == 2


@pytest.mark.asyncio
async def test_no_prompt_skips_the_guard():
    cache = make(guard=True)
    await store(cache, "Convert 10 USD to INR", "830 INR")
    assert cache.lookup(await cache.embed("Convert 10 INR to USD")).answer.text == "830 INR"
