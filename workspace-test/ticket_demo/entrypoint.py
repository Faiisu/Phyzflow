from .prediction import predict_category


def prepare(text: str) -> str:
    return text.strip().lower()


def predict(prepared: str) -> str:
    return predict_category(prepared)


def decide(prepared: str, category: str) -> dict[str, str]:
    priority = "urgent" if "urgent" in prepared else "standard"
    return {"category": category, "priority": priority}
