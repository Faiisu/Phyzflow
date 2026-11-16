"""Train the trial classifier from the repository's synthetic labeled examples."""

import json
from pathlib import Path
import pickle

from sklearn.feature_extraction.text import CountVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    examples = json.loads((directory / "training.json").read_bytes())
    texts = [text for tickets in examples.values() for text in tickets]
    labels = [category for category, tickets in examples.items() for _ in tickets]

    classifier = make_pipeline(CountVectorizer(), MultinomialNB())
    classifier.fit(texts, labels)

    with (directory / "model.pkl").open("wb") as artifact:
        pickle.dump(classifier, artifact)
