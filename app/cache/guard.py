"""
Guard for the semantic cache: cheap word checks that veto a hit embeddings can't see.

Embeddings mostly capture WHICH words a question uses, not how they relate, so
"convert 5 miles to km" and "convert 5 km to miles" look almost identical (0.99).
Before a semantic hit is returned, the two prompts must agree on:

1. Numbers: the same numbers (digits, also inside words like "gpt4", number words, ordinals).
2. Negation: both negated or both not ("which foods are safe" vs "... not safe").
3. Direction: the same word after "to" / "into" / "from" / "than" / "vs", when both use it
   ("USD to INR" vs "INR to USD", "Python faster than Java" vs "Java faster than Python").
   "to" right after words like "how" / "want" / "need" is skipped: that's "how to convert", not a direction.

Any disagreement = treat as a miss. These are rules of thumb, not understanding:
they can't catch "largest" vs "smallest" and may block a few true paraphrases.

Each prompt is turned into its Features once (linear time in its length); comparing
two Features is then cheap, so a cached prompt's features are computed when it's stored.
"""
import re
from dataclasses import dataclass

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20", "hundred": "100",
    "thousand": "1000", "million": "1000000", "billion": "1000000000",
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
    "sixth": "6th", "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th", "last": "last",
}
DIGITS = re.compile(r"\d+(?:\.\d+)?")
ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")  # 1,000 or 12,500.5 -> commas are grouping

NEGATIONS = {
    "not", "no", "never", "none", "nor", "without", "avoid", "neither", "nothing", "nobody",
    # typed without the apostrophe
    "dont", "doesnt", "didnt", "isnt", "arent", "wasnt", "werent", "cant", "wont",
    "shouldnt", "couldnt", "wouldnt", "havent", "hasnt",
}

DIRECTION_WORDS = {"to", "into", "from", "than", "vs", "versus"}
# Words skipped when looking for the word that follows a direction word ("to a server" -> "server")
FILLER = {"a", "an", "the", "my", "your", "our", "their", "his", "her", "its", "this", "that", "some"}
# "to" after these starts a verb ("how to", "want to"), not a destination
TO_VERB_BEFORE = {"how", "want", "wants", "need", "needs", "like", "able", "going", "have", "has", "trying", "try", "way"}

TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|[a-z0-9]+(?:\.[0-9]+)*")


@dataclass(frozen=True)
class Features:
    numbers: tuple[str, ...]
    negated: bool
    directions: dict[str, tuple[str, ...]]


def tokens(prompt: str) -> list[str]:
    text = prompt.lower().replace("’", "'")
    text = re.sub(r"\bcannot\b", "can not", text)
    text = re.sub(r"n't\b", " not", text)  # don't -> do not, can't -> ca not (only "not" matters)
    return TOKEN.findall(text)


def numbers(words: list[str]) -> list[str]:
    found = []
    for word in words:
        if word in NUMBER_WORDS:
            found.append(NUMBER_WORDS[word])
        elif THOUSANDS.fullmatch(word):
            found.append(word.replace(",", ""))
        elif ORDINAL.match(word):
            found.append(word)
        else:
            found.extend(DIGITS.findall(word))  # "5", "2.5", and digits inside words: gpt4 -> 4
    return sorted(found)


def is_negated(words: list[str]) -> bool:
    return any(word in NEGATIONS for word in words)


def directions(words: list[str]) -> dict[str, list[str]]:
    """For each direction word, the next real word after it: {"to": ["inr"], "from": [...]}."""
    found: dict[str, list[str]] = {}
    for i, word in enumerate(words):
        if word not in DIRECTION_WORDS:
            continue
        if word == "to" and i > 0 and words[i - 1] in TO_VERB_BEFORE:
            continue
        j = i + 1
        while j < len(words) and words[j] in FILLER:  # walk forward; no slicing, so long prompts stay linear
            j += 1
        if j < len(words):
            found.setdefault(word, []).append(words[j])
    return found


def features(prompt: str) -> Features:
    words = tokens(prompt)
    return Features(
        numbers=tuple(numbers(words)),
        negated=is_negated(words),
        directions={k: tuple(sorted(v)) for k, v in directions(words).items()},
    )


def features_conflict(a: Features, b: Features) -> str | None:
    """Why a cached answer for `a` must not be reused for `b`, or None if no rule objects."""
    if a.numbers != b.numbers:
        return "numbers"
    if a.negated != b.negated:
        return "negation"
    for word in a.directions.keys() & b.directions.keys():
        if a.directions[word] != b.directions[word]:
            return "direction"
    return None


def conflict(a: str, b: str) -> str | None:
    return features_conflict(features(a), features(b))
