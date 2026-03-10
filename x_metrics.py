#!/usr/bin/env python3
"""CLI tool to fetch X/Twitter tweet metrics via API v2."""

import os
import re
import sys
from typing import Any, Dict, Optional

import requests

API_BASE = "https://api.x.com/2/tweets"


class XMetricsError(Exception):
    """Custom exception for user-friendly CLI errors."""


def extract_tweet_id(url: str) -> str:
    """Extract tweet ID from x.com/twitter.com URL."""
    pattern = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/(?:status|statuses)/(\d+)")
    match = pattern.search(url.strip())
    if not match:
        raise XMetricsError(
            "Invalid X/Twitter URL. Expected format like "
            "https://x.com/<user>/status/<tweet_id>"
        )
    return match.group(1)


def call_tweet_api(tweet_id: str, bearer_token: str) -> Dict[str, Any]:
    """Fetch tweet object with public metrics and references."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "tweet.fields": "public_metrics,referenced_tweets",
    }
    url = f"{API_BASE}/{tweet_id}"

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
    except requests.RequestException as exc:
        raise XMetricsError(f"Network error while calling X API: {exc}") from exc

    if resp.status_code == 401:
        raise XMetricsError("Unauthorized (401). Please check X_BEARER_TOKEN.")
    if resp.status_code == 404:
        raise XMetricsError(f"Tweet not found (404): {tweet_id}")
    if not resp.ok:
        msg = resp.text.strip()
        raise XMetricsError(f"X API request failed ({resp.status_code}): {msg}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise XMetricsError("X API returned invalid JSON.") from exc

    data = payload.get("data")
    if not data:
        raise XMetricsError(f"No tweet data returned for ID: {tweet_id}")

    return data


def is_repost_and_original_id(tweet_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Check whether tweet is a retweet/repost and return original tweet ID."""
    refs = tweet_data.get("referenced_tweets") or []
    for ref in refs:
        if ref.get("type") == "retweeted":
            return True, ref.get("id")
    return False, None


def print_metrics(title: str, tweet_id: str, metrics: Dict[str, Any]) -> None:
    """Pretty print public metrics."""
    print(f"{title}:")
    print(f"  Tweet ID: {tweet_id}")
    print(f"  Likes: {metrics.get('like_count', 0)}")
    print(f"  Replies: {metrics.get('reply_count', 0)}")
    print(f"  Reposts: {metrics.get('retweet_count', 0)}")
    print(f"  Quotes: {metrics.get('quote_count', 0)}")


def main() -> int:
    if len(sys.argv) != 2:
        print('Usage: python x_metrics.py "https://x.com/<user>/status/<tweet_id>"')
        return 1

    input_url = sys.argv[1]

    bearer_token = os.getenv("X_BEARER_TOKEN")
    if not bearer_token:
        print("Error: X_BEARER_TOKEN is not set.")
        return 1

    try:
        tweet_id = extract_tweet_id(input_url)
        current_data = call_tweet_api(tweet_id, bearer_token)

        is_repost, original_id = is_repost_and_original_id(current_data)
        current_metrics = current_data.get("public_metrics", {})

        print(f"Input URL: {input_url}")
        print(f"Tweet ID: {tweet_id}")
        print(f"Is repost: {'Yes' if is_repost else 'No'}")
        print_metrics("Current post metrics", tweet_id, current_metrics)

        if is_repost and original_id:
            original_data = call_tweet_api(original_id, bearer_token)
            original_metrics = original_data.get("public_metrics", {})
            print_metrics("Original post metrics", original_id, original_metrics)

    except XMetricsError as err:
        print(f"Error: {err}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
