"""
LLM-based sentiment classifier for presidential Iran-related posts.
"""

from openai import OpenAI
from enum import Enum
import os


class Sentiment(Enum):
    BELLICOSE = "bellicose"
    CONCILIATORY = "conciliatory"
    MIXED = "mixed"
    NEUTRAL = "neutral"


class IranSentimentClassifier:
    """
    Classifies presidential posts related to Iran into sentiment categories.
    Distinguishes pure conciliatory signals from mixed messages.
    """

    SYSTEM_PROMPT = """You are a geopolitical sentiment analyst specializing in
analyzing government and official rhetoric regarding Iran and Middle East tensions.

Posts may be written in English, Persian (Farsi), Hebrew, or Arabic.
Classify the sentiment regardless of the language the post is written in —
translate internally if needed, then apply the same criteria below.

Classify the post into one of four categories:
- BELLICOSE: Threatening language, military posturing, escalation, sanctions threats,
  "all options on table", "will not tolerate", warnings about consequences,
  military deployments announced, aggressive negotiations stance
- CONCILIATORY: PURELY diplomatic language with NO threats or military references.
  Peace overtures, "we want peace", "looking for solutions", "willing to talk",
  detente signals, reduced tensions. The post must be WHOLLY positive about
  diplomacy/peace with ZERO bellicose undertones.
- MIXED: Contains BOTH conciliatory AND bellicose elements. For example: announcing
  a pause on strikes (conciliatory) while referencing "energy plant destruction"
  (bellicose), or discussing negotiations while threatening consequences if they fail.
  Any post that pairs diplomacy with threats or military language is MIXED.
- NEUTRAL: Factual reporting, neither escalatory nor de-escalatory, or not about Iran.

Respond with ONLY a single word: bellicose, conciliatory, mixed, or neutral."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model

    def classify(self, text: str) -> Sentiment:
        """
        Classify a post's sentiment regarding Iran.

        Args:
            text: The post/tweet content to analyze

        Returns:
            Sentiment enum value
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            max_tokens=10,
            temperature=0
        )

        result = response.choices[0].message.content.strip().lower()

        if "bellicose" in result:
            return Sentiment.BELLICOSE
        elif "mixed" in result:
            return Sentiment.MIXED
        elif "conciliatory" in result:
            return Sentiment.CONCILIATORY
        else:
            return Sentiment.NEUTRAL


if __name__ == "__main__":
    # Quick test
    classifier = IranSentimentClassifier()

    test_posts = [
        "We will not tolerate Iran's nuclear program. All options are on the table.",
        "We're committed to a peaceful resolution through diplomacy.",
        "The President met with advisors today to discuss various matters.",
    ]

    for post in test_posts:
        sentiment = classifier.classify(post)
        print(f"Post: {post[:50]}...")
        print(f"Sentiment: {sentiment.value}\n")