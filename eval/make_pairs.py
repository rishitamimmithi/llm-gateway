"""Writes eval/pairs.jsonl: question pairs labelled same meaning (true) or different meaning (false)."""
import json
from pathlib import Path

# same meaning: a cached answer for `a` is a correct answer for `b`
SAME = {
    "paraphrase": [
        ("What is the capital of France?", "Which city is the capital of France?"),
        ("How do I reverse a list in Python?", "What's the way to reverse a Python list?"),
        ("Explain photosynthesis in simple terms.", "Can you explain photosynthesis simply?"),
        ("What is the boiling point of water at sea level?", "At sea level, what temperature does water boil at?"),
        ("Who wrote Pride and Prejudice?", "Who is the author of Pride and Prejudice?"),
        ("How many continents are there?", "What is the number of continents on Earth?"),
        ("What does HTTP stand for?", "What is the full form of HTTP?"),
        ("Give me a recipe for pancakes.", "Can you share a pancake recipe?"),
        ("What is the speed of light?", "How fast does light travel?"),
        ("How do I make a git branch?", "How can I create a new branch in git?"),
        ("What causes rain?", "Why does it rain?"),
        ("Translate 'thank you' into Spanish.", "How do you say 'thank you' in Spanish?"),
        ("What is machine learning?", "Can you explain what machine learning is?"),
        ("What is the tallest mountain in the world?", "Which mountain is the highest on Earth?"),
        ("How do I center a div in CSS?", "What's the way to center a div using CSS?"),
        ("What is the difference between a list and a tuple in Python?", "How is a Python tuple different from a list?"),
        ("Summarise the plot of Hamlet.", "Give me a summary of Hamlet's plot."),
        ("What are the symptoms of the flu?", "What symptoms does influenza cause?"),
        ("How many bones are in the human body?", "How many bones does an adult human have?"),
        ("What is an API?", "Explain what an API is."),
        ("How do I install numpy?", "What's the command to install numpy?"),
        ("Why is the sky blue?", "What makes the sky look blue?"),
        ("What is the largest ocean?", "Which ocean is the biggest?"),
        ("How long does it take to boil an egg?", "How many minutes should I boil an egg?"),
        ("What is a black hole?", "Can you explain what a black hole is?"),
        ("Write a haiku about autumn.", "Compose a haiku about the autumn season."),
        ("What is the chemical symbol for gold?", "Which element symbol stands for gold?"),
        ("How do I read a file in Python?", "How can I open and read a file using Python?"),
        ("What is inflation?", "Explain inflation in economics."),
        ("Who painted the Mona Lisa?", "Which artist painted the Mona Lisa?"),
    ],
    "formatting": [
        ("what is the capital of japan", "What is the capital of Japan?"),
        ("How do I sort a dictionary by value in Python??", "how do i sort a dictionary by value in python"),
        ("Explain recursion", "explain recursion."),
        ("WHAT IS DNA?", "What is DNA?"),
        ("define entropy", "Define entropy."),
        ("whats the population of india", "What's the population of India?"),
        ("Tell me a joke about cats", "tell me a joke about cats!"),
        ("Hi, what is Docker?", "What is Docker?"),
        ("Please explain what a REST API is", "Explain what a REST API is"),
        ("can u explain big o notation", "Can you explain Big O notation?"),
    ],
    "word_order": [
        ("In Python, how do I remove duplicates from a list?", "How do I remove duplicates from a list in Python?"),
        ("For a beginner, what is the best way to learn guitar?", "What is the best way to learn guitar for a beginner?"),
        ("At what temperature does ice melt?", "Ice melts at what temperature?"),
        ("Using SQL, how do I count rows in a table?", "How do I count rows in a table using SQL?"),
        ("In JavaScript, what is a closure?", "What is a closure in JavaScript?"),
        ("For dinner tonight, suggest a vegetarian meal.", "Suggest a vegetarian meal for dinner tonight."),
        ("On Linux, how do I check disk space?", "How do I check disk space on Linux?"),
        ("In the story of Romeo and Juliet, how does Juliet die?", "How does Juliet die in Romeo and Juliet?"),
        ("For a 5 km run, how should I warm up?", "How should I warm up for a 5 km run?"),
        ("In Excel, how do I freeze the top row?", "How do I freeze the top row in Excel?"),
    ],
}

# different meaning: a cached answer for `a` would be WRONG for `b` (a false hit)
DIFFERENT = {
    "direction_swap": [
        ("Convert 10 USD to INR", "Convert 10 INR to USD"),
        ("Convert 100 Celsius to Fahrenheit", "Convert 100 Fahrenheit to Celsius"),
        ("Translate 'hello' from English to French", "Translate 'hello' from French to English"),
        ("How long is the train from Delhi to Mumbai?", "How long is the train from Mumbai to Delhi?"),
        ("Convert 5 miles to kilometres", "Convert 5 kilometres to miles"),
        ("Convert 50 EUR to GBP", "Convert 50 GBP to EUR"),
        ("How do I convert a string to an int in Python?", "How do I convert an int to a string in Python?"),
        ("Convert JSON to CSV in Python", "Convert CSV to JSON in Python"),
        ("Is Python faster than Java?", "Is Java faster than Python?"),
        ("Who did Argentina beat in the 2022 World Cup final?", "Who beat Argentina in the 2014 World Cup final?"),
        ("Convert 3 pounds to kilograms", "Convert 3 kilograms to pounds"),
        ("How do I copy files from my laptop to a server with scp?", "How do I copy files from a server to my laptop with scp?"),
    ],
    "number_change": [
        ("What is 15% of 200?", "What is 20% of 150?"),
        ("Convert 10 USD to INR", "Convert 100 USD to INR"),
        ("What is the square root of 144?", "What is the square root of 169?"),
        ("What happened in the year 1945?", "What happened in the year 1954?"),
        ("How many days are in 3 weeks?", "How many days are in 5 weeks?"),
        ("Write a 100-word story about a dog.", "Write a 500-word story about a dog."),
        ("What is 7 times 8?", "What is 7 times 9?"),
        ("Give me 3 tips for better sleep.", "Give me 10 tips for better sleep."),
        ("Is 91 a prime number?", "Is 97 a prime number?"),
        ("What is 2 to the power of 10?", "What is 2 to the power of 16?"),
        ("Plan a 2-day trip to Paris.", "Plan a 7-day trip to Paris."),
        ("What was the population of the US in 1990?", "What was the population of the US in 2020?"),
    ],
    "entity_swap": [
        ("What is the capital of Australia?", "What is the capital of Austria?"),
        ("Who is the CEO of Microsoft?", "Who is the CEO of Google?"),
        ("How do I reverse a list in Python?", "How do I reverse a list in JavaScript?"),
        ("What is the population of Canada?", "What is the population of Mexico?"),
        ("When was Albert Einstein born?", "When was Isaac Newton born?"),
        ("What are the health benefits of green tea?", "What are the health benefits of black coffee?"),
        ("Explain the French Revolution.", "Explain the Russian Revolution."),
        ("What is the boiling point of water?", "What is the boiling point of ethanol?"),
        ("Who wrote Hamlet?", "Who wrote Don Quixote?"),
        ("How tall is Mount Everest?", "How tall is K2?"),
        ("What language is spoken in Brazil?", "What language is spoken in Argentina?"),
        ("How do I install Docker on Ubuntu?", "How do I install Docker on Windows?"),
        ("What is the time complexity of quicksort?", "What is the time complexity of merge sort?"),
        ("Summarise the plot of Hamlet.", "Summarise the plot of Macbeth."),
        ("What is the currency of Japan?", "What is the currency of China?"),
    ],
    "negation": [
        ("Which foods are safe for dogs?", "Which foods are not safe for dogs?"),
        ("Why should I use microservices?", "Why should I not use microservices?"),
        ("What are the advantages of nuclear energy?", "What are the disadvantages of nuclear energy?"),
        ("List the countries that are in the EU.", "List European countries that are not in the EU."),
        ("Which animals can swim?", "Which animals cannot swim?"),
        ("When should I use recursion?", "When should I avoid recursion?"),
        ("What are the pros of remote work?", "What are the cons of remote work?"),
        ("Which mushrooms are edible?", "Which mushrooms are poisonous?"),
        ("Why is sugar good for you?", "Why is sugar bad for you?"),
        ("Is it a good idea to store passwords in plain text?", "Why is it a bad idea to hash passwords?"),
    ],
    "comparison_or_superlative": [
        ("What is the largest planet in the solar system?", "What is the smallest planet in the solar system?"),
        ("What is the longest river in the world?", "What is the shortest river in the world?"),
        ("Which is the most spoken language?", "Which is the least spoken language?"),
        ("What is the hottest planet?", "What is the coldest planet?"),
        ("Which sorting algorithm is the fastest?", "Which sorting algorithm is the slowest?"),
        ("What is the deepest lake in the world?", "What is the largest lake in the world?"),
        ("Who is the richest person in the world?", "Who is the oldest person in the world?"),
        ("What is the oldest university in Europe?", "What is the newest university in Europe?"),
    ],
    "time_or_order": [
        ("What is the weather like today in London?", "What will the weather be like tomorrow in London?"),
        ("Who won the 2022 FIFA World Cup?", "Who won the 2018 FIFA World Cup?"),
        ("Who was the first person on the Moon?", "Who was the second person on the Moon?"),
        ("What did people eat in medieval Europe?", "What do people eat in modern Europe?"),
        ("Explain Python 2 print syntax.", "Explain Python 3 print syntax."),
        ("Summarise chapter 1 of the book.", "Summarise chapter 2 of the book."),
        ("What is the first law of thermodynamics?", "What is the second law of thermodynamics?"),
        ("Who was the first US president?", "Who was the third US president?"),
    ],
    "task_change": [
        ("Write a poem about the ocean.", "Write a story about the ocean."),
        ("Summarise this: the cat sat on the mat.", "Translate this: the cat sat on the mat."),
        ("Explain how a car engine works.", "Explain how to fix a car engine."),
        ("Write a Python function to sort a list.", "Write a Python function to shuffle a list."),
        ("Give me a recipe for vegan pancakes.", "Give me a recipe for gluten-free pancakes."),
        ("Write a formal email asking for leave.", "Write a casual email asking for leave."),
        ("Explain recursion to a 5-year-old.", "Explain recursion to a PhD student."),
        ("Write a SQL query to delete duplicate rows.", "Write a SQL query to find duplicate rows."),
    ],
}


def main() -> None:
    out = Path(__file__).with_name("pairs.jsonl")
    rows = [
        {"a": a, "b": b, "same": same, "category": category}
        for same, groups in ((True, SAME), (False, DIFFERENT))
        for category, pairs in groups.items()
        for a, b in pairs
    ]
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    same = sum(r["same"] for r in rows)
    print(f"wrote {len(rows)} pairs ({same} same, {len(rows) - same} different) to {out}")


if __name__ == "__main__":
    main()
