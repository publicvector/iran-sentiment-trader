"""
LLM-based sentiment classifier for presidential Iran-related posts.
"""

from openai import OpenAI
from enum import Enum
import os


class Sentiment(Enum):
    BELLICOSE = "bellicose"
    CONCILIATORY = "conciliatory"
    NEUTRAL = "neutral"


class IranSentimentClassifier:
    """
    Classifies presidential posts related to Iran into sentiment categories.
    """

    SYSTEM_PROMPT = """You are a geopolitical sentiment analyst specializing in 
analyzing presidential rhetoric regarding Iran and Middle East tensions.

Classify the post into one of three categories:
- BELLICOSE: Threatening language, military posturing, escalation, sanctions threats, 
  "all options on table", "will not tolerate", warnings about consequences, 
  military deployments announced, aggressive negotiations stance
- CONCILIATORY: Diplomatic language, de-escalation, peace overtures, "we want peace",
  "looking for solutions", "willing to talk", detente signals, reduced tensions
- NEUTRAL: Factual reporting, neither escalatory nor de-escalatory

Respond with ONLY a single word: bellicose, conciliatory, or neutral."""

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