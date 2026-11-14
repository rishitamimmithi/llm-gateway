# Semantic cache false-hit evaluation

Model: `sentence-transformers/all-MiniLM-L6-v2`, 15 same-meaning pairs, 15 different-meaning pairs.

Before = similarity only. After = similarity + guard (numbers, negation, direction).

| threshold | hit rate before | false-hit rate before | precision before | hit rate after | false-hit rate after | precision after |
|---|---|---|---|---|---|---|
| 0.70 | 100.0% | 93.3% | 51.7% | 93.3% | 33.3% | 73.7% |
| 0.75 | 100.0% | 93.3% | 51.7% | 93.3% | 33.3% | 73.7% |
| 0.80 | 93.3% | 80.0% | 53.8% | 86.7% | 26.7% | 76.5% |
| 0.85 | 66.7% | 73.3% | 47.6% | 66.7% | 20.0% | 76.9% |
| 0.88 | 60.0% | 66.7% | 47.4% | 60.0% | 13.3% | 81.8% |
| 0.90 | 53.3% | 60.0% | 47.1% | 53.3% | 13.3% | 80.0% |
| 0.92 | 53.3% | 53.3% | 50.0% | 53.3% | 6.7% | 88.9% |
| 0.94 | 40.0% | 40.0% | 50.0% | 40.0% | 6.7% | 85.7% |
| 0.96 | 40.0% | 33.3% | 54.5% | 40.0% | 6.7% | 85.7% |
| 0.98 | 20.0% | 33.3% | 37.5% | 20.0% | 6.7% | 75.0% |

## What the guard caught

| category | same? | pairs | blocked | reasons |
|---|---|---|---|---|
| formatting | True | 1 | 0 | - |
| paraphrase | True | 11 | 1 | numbers |
| word_order | True | 3 | 0 | - |
| comparison_or_superlative | False | 1 | 0 | - |
| direction_swap | False | 5 | 4 | direction |
| entity_swap | False | 2 | 0 | - |
| negation | False | 3 | 2 | negation |
| number_change | False | 3 | 3 | numbers |
| task_change | False | 1 | 0 | - |

## Similarity by category

| category | same? | pairs | min | mean | max |
|---|---|---|---|---|---|
| formatting | True | 1 | 0.841 | 0.841 | 0.841 |
| paraphrase | True | 11 | 0.778 | 0.887 | 0.966 |
| word_order | True | 3 | 0.982 | 0.985 | 0.988 |
| comparison_or_superlative | False | 1 | 0.859 | 0.859 | 0.859 |
| direction_swap | False | 5 | 0.986 | 0.993 | 0.999 |
| entity_swap | False | 2 | 0.661 | 0.718 | 0.774 |
| negation | False | 3 | 0.914 | 0.935 | 0.959 |
| number_change | False | 3 | 0.791 | 0.872 | 0.927 |
| task_change | False | 1 | 0.805 | 0.805 | 0.805 |

## Most similar different-meaning pairs (the dangerous ones)

| similarity | guard | a | b |
|---|---|---|---|
| 0.999 | direction | Translate 'good morning' from German into Italian | Translate 'good morning' from Italian into German |
| 0.996 | direction | How do I send money from PayPal to my bank? | How do I send money from my bank to PayPal? |
| 0.995 | let through | What is 12 divided by 4? | What is 4 divided by 12? |
| 0.991 | direction | Convert 20 kg to pounds | Convert 20 pounds to kg |
| 0.986 | direction | Is a cat smarter than a dog? | Is a dog smarter than a cat? |
| 0.959 | negation | Which vitamins dissolve in water? | Which vitamins don't dissolve in water? |
| 0.933 | negation | What should I pack for a hiking trip? | What should I not pack for a hiking trip? |
| 0.927 | numbers | Write a five-line poem about rain. | Write a ten-line poem about rain. |
| 0.914 | let through | Is coffee healthy? | Is coffee unhealthy? |
| 0.898 | numbers | Who won the Champions League in 2019? | Who won the Champions League in 2021? |
| 0.859 | let through | What is the most populated country? | What is the least populated country? |
| 0.805 | let through | Explain how to start a small business. | Explain how to close a small business. |
| 0.791 | numbers | What is 25% of 80? | What is 25% of 60? |
| 0.774 | let through | What is the capital of Slovakia? | What is the capital of Slovenia? |
| 0.661 | let through | How do I sort an array in Java? | How do I sort an array in Go? |

## Least similar same-meaning pairs (missed savings)

| similarity | a | b |
|---|---|---|
| 0.778 | Give me two examples of renewable energy. | Name 2 renewable energy sources. |
| 0.819 | How do I merge two dictionaries in Python? | What's the way to combine two Python dicts? |
| 0.821 | How do I delete a folder in Linux? | What's the command to remove a directory on Linux? |
| 0.841 | What does CPU stand for? | what is the full form of cpu |
| 0.843 | How do I undo my last git commit? | How can I revert the most recent commit in git? |
| 0.866 | What is the main function of the liver? | What does the liver mainly do? |
| 0.882 | Why do leaves change colour in autumn? | What makes leaves turn colour in the fall? |
| 0.924 | How far is the Moon from Earth? | What is the distance between the Earth and the Moon? |
| 0.932 | What is the capital of Canada? | Which city is Canada's capital? |
| 0.963 | What is a hash table? | Explain what a hash table is. |
