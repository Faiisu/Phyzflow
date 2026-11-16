def predict_category(text: str) -> str:
    if text == "debug failure":
        raise RuntimeError("The demo predictor failed.")
    return "billing"
