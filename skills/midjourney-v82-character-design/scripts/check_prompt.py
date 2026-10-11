"""Check exact prompt length and optional immutable prefix; no dependencies."""

import argparse
import json
import sys


LIMIT = 1024


def check_prompt(prompt, prefix=""):
    if not isinstance(prompt, str) or not isinstance(prefix, str):
        raise ValueError("prompt and prefix must be strings")
    count = len(prompt)
    nonempty = bool(prompt.strip())
    prefix_matches = prompt.startswith(prefix)
    return {
        "characters": count,
        "limit": LIMIT,
        "remaining": LIMIT - count,
        "prefix_characters": len(prefix),
        "prefix_matches": prefix_matches,
        "within_limit": count <= LIMIT,
        "valid": nonempty and count <= LIMIT and prefix_matches,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="UTF-8 JSON file; otherwise read stdin")
    args = parser.parse_args()
    try:
        if args.file:
            with open(args.file, encoding="utf-8-sig", newline="") as source:
                payload = json.load(source)
        else:
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if not isinstance(payload, dict) or "prompt" not in payload:
            raise ValueError('input must be an object with a "prompt" string')
        result = check_prompt(payload["prompt"], payload.get("prefix", ""))
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
