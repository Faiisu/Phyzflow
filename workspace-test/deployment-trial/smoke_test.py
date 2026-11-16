"""Verify the serving contract over HTTP using held-out ticket examples."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def post(base_url: str, payload: object) -> tuple[int, object]:
    request = Request(
        f"{base_url.rstrip('/')}/predict",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        status = response.status
        if status is None:
            raise RuntimeError("Expected an HTTP response.")
        return status, json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    args = parser.parse_args()
    cases = (
        ({"text": "Urgent: duplicate invoice charge"}, 200, {"category": "billing", "priority": "urgent"}),
        ({"text": "Please reset my account login password"}, 200, {"category": "access", "priority": "standard"}),
        ({"text": "The app crashes with a blank screen"}, 200, {"category": "technical", "priority": "standard"}),
        ({"text": 123}, 400, {"error": "Provide a non-empty text string."}),
    )
    for payload, expected_status, expected_body in cases:
        actual = post(args.base_url, payload)
        if actual != (expected_status, expected_body):
            raise SystemExit(f"Unexpected response for {payload!r}: {actual!r}")
    print("Verified three held-out predictions and invalid-input rejection.")


if __name__ == "__main__":
    main()
