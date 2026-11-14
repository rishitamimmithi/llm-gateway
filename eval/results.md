# Semantic cache false-hit evaluation

Model: `sentence-transformers/all-MiniLM-L6-v2`, 50 same-meaning pairs, 73 different-meaning pairs.

| threshold | hit rate | false-hit rate | precision | good hits | false hits |
|---|---|---|---|---|---|
| 0.70 | 100.0% | 84.9% | 44.6% | 50 | 62 |
| 0.75 | 100.0% | 69.9% | 49.5% | 50 | 51 |
| 0.80 | 98.0% | 63.0% | 51.6% | 49 | 46 |
| 0.85 | 90.0% | 50.7% | 54.9% | 45 | 37 |
| 0.88 | 88.0% | 39.7% | 60.3% | 44 | 29 |
| 0.90 | 78.0% | 32.9% | 61.9% | 39 | 24 |
| 0.92 | 68.0% | 28.8% | 61.8% | 34 | 21 |
| 0.94 | 60.0% | 21.9% | 65.2% | 30 | 16 |
| 0.96 | 44.0% | 13.7% | 68.8% | 22 | 10 |
| 0.98 | 30.0% | 12.3% | 62.5% | 15 | 9 |

## Similarity by category

| category | same? | pairs | min | mean | max |
|---|---|---|---|---|---|
| formatting | True | 10 | 0.901 | 0.974 | 1.000 |
| paraphrase | True | 30 | 0.758 | 0.910 | 0.977 |
| word_order | True | 10 | 0.961 | 0.987 | 0.996 |
| comparison_or_superlative | False | 8 | 0.553 | 0.806 | 0.949 |
| direction_swap | False | 12 | 0.830 | 0.968 | 0.999 |
| entity_swap | False | 15 | 0.509 | 0.690 | 0.868 |
| negation | False | 10 | 0.559 | 0.885 | 0.966 |
| number_change | False | 12 | 0.658 | 0.820 | 0.949 |
| task_change | False | 8 | 0.659 | 0.807 | 0.941 |
| time_or_order | False | 8 | 0.734 | 0.864 | 0.939 |

## Most similar different-meaning pairs (the dangerous ones)

| similarity | a | b |
|---|---|---|
| 0.999 | How do I copy files from my laptop to a server with scp? | How do I copy files from a server to my laptop with scp? |
| 0.998 | How do I convert a string to an int in Python? | How do I convert an int to a string in Python? |
| 0.998 | Translate 'hello' from English to French | Translate 'hello' from French to English |
| 0.997 | How long is the train from Delhi to Mumbai? | How long is the train from Mumbai to Delhi? |
| 0.996 | Convert JSON to CSV in Python | Convert CSV to JSON in Python |
| 0.994 | Convert 3 pounds to kilograms | Convert 3 kilograms to pounds |
| 0.993 | Convert 5 miles to kilometres | Convert 5 kilometres to miles |
| 0.993 | Is Python faster than Java? | Is Java faster than Python? |
| 0.991 | Convert 100 Celsius to Fahrenheit | Convert 100 Fahrenheit to Celsius |
| 0.966 | Which foods are safe for dogs? | Which foods are not safe for dogs? |
| 0.959 | Why should I use microservices? | Why should I not use microservices? |
| 0.957 | Convert 10 USD to INR | Convert 10 INR to USD |
| 0.949 | Which sorting algorithm is the fastest? | Which sorting algorithm is the slowest? |
| 0.949 | Give me 3 tips for better sleep. | Give me 10 tips for better sleep. |
| 0.948 | When should I use recursion? | When should I avoid recursion? |

## Least similar same-meaning pairs (missed savings)

| similarity | a | b |
|---|---|---|
| 0.758 | Give me a recipe for pancakes. | Can you share a pancake recipe? |
| 0.813 | What is inflation? | Explain inflation in economics. |
| 0.828 | What is the chemical symbol for gold? | Which element symbol stands for gold? |
| 0.832 | What is the speed of light? | How fast does light travel? |
| 0.844 | What are the symptoms of the flu? | What symptoms does influenza cause? |
| 0.879 | What does HTTP stand for? | What is the full form of HTTP? |
| 0.883 | How many bones are in the human body? | How many bones does an adult human have? |
| 0.886 | What is the tallest mountain in the world? | Which mountain is the highest on Earth? |
| 0.887 | Translate 'thank you' into Spanish. | How do you say 'thank you' in Spanish? |
| 0.891 | What causes rain? | Why does it rain? |
