import json
import os
import time
from typing import Any, Dict, Optional

import requests

HC_URL = "https://api.hardcover.app/v1/graphql"


def _read_cache(path: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        if not os.path.exists(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > ttl_seconds:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(path: str, payload: Dict[str, Any]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def graphql(token: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
    }
    r = requests.post(
        HC_URL,
        headers=headers,
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def fetch_hardcover_data(token: str, cache_path: str, ttl_seconds: int, nocache: bool) -> Dict[str, Any]:
    if not nocache:
        cached = _read_cache(cache_path, ttl_seconds)
        if cached is not None:
            return cached

    # IMPORTANT:
    # - user_book_reads.updated_at does NOT exist in your schema -> removed.
    # - book.genres does NOT exist in your schema -> omitted.
    QUERY = """
    query GetReadingData {
      me {
        username
        name
        image { url }

        goals(where: { state: { _eq: "active" } }, limit: 1) {
          goal
          progress
        }

        currently_reading: user_books(
          where: { status_id: { _eq: 2 } }
          order_by: { updated_at: desc }
        ) {
          id
          updated_at
          rating
          last_read_date
          user_book_reads {
            started_at
            progress
            finished_at
          }
          book {
            title
            slug
            pages
            image { url }
            contributions { author { name } }
          }
        }

        recently_read: user_books(
          where: { status_id: { _eq: 3 } }
          order_by: { last_read_date: desc }
        ) {
          id
          updated_at
          rating
          has_review
          last_read_date
          user_book_reads {
            started_at
            progress
            finished_at
          }
          book {
            title
            slug
            pages
            image { url }
            contributions { author { name } }
          }
        }
      }
    }
    """

    data = graphql(token, QUERY, variables={})
    payload = {"me": data.get("me")}
    _write_cache(cache_path, payload)
    return payload
