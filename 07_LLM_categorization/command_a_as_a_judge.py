"""
LLM-as-a-Judge: classify environmental actions on synagogue web pages.

Two models available:
  get_response()          — Cohere command-a-plus-05-2026  (COHERE_API_KEY)
  get_response_nemotron() — nvidia/nemotron-3-super-120b-a12b via OpenRouter (NEMOTRON_API_KEY)

Requires ../.env with the relevant key(s) set.
"""

import os
from pathlib import Path

import cohere
from openai import OpenAI
from dotenv import load_dotenv

# ── load credentials ──────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

co = cohere.ClientV2(api_key=os.getenv("COHERE_API_KEY"))

nemotron = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("NEMOTRON_API_KEY"),
)

# ── prompt builder ────────────────────────────────────────────────────────────

def build_prompt(website_content: str) -> str:
    return f"""#Context: The data comes from US synagogue websites, your task is to classify these contents
# Task: Classify the kind of environmental action discussed on this web page and output the name of the classification category (please only choose from the following nine categories, and only return "Community","Spirituality and Worship","Kitchen","Waste","Energy", "Operations and Maintenance", "Environmental and Climate Justice", "Water", or "Other"). No explanation is needed. If the action is not actually an environmental action, please return "N/A".
# Categories include:
1. Community: environmental actions focused on building community.
2. Spirituality and Worship: actions directly related to spirituality and the practice of religion.
3. Kitchen: actions related to the kitchen and food waste.
4. Waste: actions focused on reducing waste or in some way making it more sustainable.
5. Energy: actions which involve reducing energy consumption or sourcing more environmentally friendly power sources.
6. Operations and Maintenance: actions which rebuild or somehow change the structure and day to day operations of the synagogue to be more sustainable.
7. Environmental and Climate Justice: these are environmental actions which are specifically conducted to increase fairness and create a more just sustainable society.
8. Water: actions which involve reducing the use of water.
9. Other.
10. N/A: if the action is not actually an environmental action, please return "N/A".
User thread content is as follows:
\"\"\"{website_content}\"\"\"
Please return the name of the classification category for this thread"""


# ── inference ─────────────────────────────────────────────────────────────────

def get_response(text: str) -> str:
    """Classify the environmental action in *text* using Cohere Command-A+."""
    import time
    prompt = build_prompt(text)
    for attempt in range(4):
        try:
            res = co.chat(
                model="command-a-plus-05-2026",
                messages=[
                    {"role": "system", "content": "You are a text classifier"},
                    {"role": "user",   "content": prompt},
                ],
            )
            # Command-A+ may prepend a thinking block; find the text item
            for item in res.message.content:
                if hasattr(item, "text"):
                    return item.text
            return res.message.content[-1].text
        except Exception as e:
            if "429" in str(e) and attempt < 3:
                time.sleep(62)
            else:
                raise


def get_response_nemotron(text: str) -> str:
    """Classify the environmental action in *text* using Nemotron via OpenRouter."""
    prompt = build_prompt(text)
    res = nemotron.chat.completions.create(
        model="nvidia/nemotron-3-super-120b-a12b:free",
        messages=[
            {"role": "system", "content": "You are a text classifier"},
            {"role": "user",   "content": prompt},
        ],
    )
    return res.choices[0].message.content
