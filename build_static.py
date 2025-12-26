#!/usr/bin/env python3
import os
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone, date
from collections import defaultdict
from jinja2 import Environment, FileSystemLoader

from hardcover_client import fetch_hardcover_data

ROOT = Path(__file__).parent.resolve()
DOCS = ROOT / "docs"
STATIC_SRC = ROOT / "static"
STATIC_DST = DOCS / "static"
TEMPLATES = ROOT / "templates"
CACHE_PATH = ROOT / ".cache" / "hardcover.json"
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "900"))

MONTH_NAMES_DE = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April",
    5: "Mai", 6: "Juni", 7: "Juli", 8: "August",
    9: "September", 10: "Oktober", 11: "November", 12: "Dezember"
}

def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def copy_static():
    if STATIC_DST.exists():
        shutil.rmtree(STATIC_DST)
    shutil.copytree(STATIC_SRC, STATIC_DST)

def normalize_me(raw_me):
    if isinstance(raw_me, list):
        if not raw_me:
            raise RuntimeError("Hardcover API returned empty `me` list")
        return raw_me[0]
    return raw_me

def parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    # Hardcover returns ISO-ish; we only need YYYY-MM-DD
    return date.fromisoformat(s[:10])

def compute_totals(finished):
    pages = 0
    missing = 0
    for b in finished:
        p = b.get("pages")
        if isinstance(p, int) and p > 0:
            pages += p
        else:
            missing += 1
    return {"books": len(finished), "pages": pages, "missing_pages": missing}

def compute_books_per_year(finished):
    counter = defaultdict(int)
    for b in finished:
        fd = b.get("finished_date")
        if fd:
            counter[fd.year] += 1
    rows = [{"year": y, "count": counter[y]} for y in sorted(counter.keys(), reverse=True)]
    maxv = max((r["count"] for r in rows), default=0)
    return rows, maxv

def compute_timeline(finished):
    # group: year -> month -> list
    years = defaultdict(lambda: defaultdict(list))
    for b in finished:
        fd = b.get("finished_date")
        if not fd:
            continue
        years[fd.year][fd.month].append(b)

    timeline = []
    for y in sorted(years.keys(), reverse=True):
        months = []
        for m in sorted(years[y].keys(), reverse=True):
            books = sorted(
                years[y][m],
                key=lambda x: (x.get("finished_date") or date.min),
                reverse=True,
            )
            months.append({
                "month": m,
                "month_name": MONTH_NAMES_DE.get(m, str(m)),
                "count": len(books),
                "books": books,
            })
        timeline.append({
            "year": y,
            "count": sum(mm["count"] for mm in months),
            "months": months,
        })
    return timeline

def main():
    token = os.getenv("HARDCOVER_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("HARDCOVER_API_TOKEN missing")

    # ✅ base path for CSS/links:
    # Org Pages: "/" (default)
    # Project Pages: "/<repo-name>/"
    base_path = os.getenv("BASE_PATH", "/").strip()
    if not base_path.endswith("/"):
        base_path += "/"
    if not base_path.startswith("/"):
        base_path = "/" + base_path

    nocache = os.getenv("NOCACHE", "1") == "1"

    raw = fetch_hardcover_data(
        token=token,
        cache_path=CACHE_PATH,
        ttl_seconds=CACHE_TTL,
        nocache=nocache,
    )

    me_raw = normalize_me(raw["me"])
    user_books = me_raw.get("user_books", [])

    currently = []
    finished = []

    for ub in user_books:
        book = ub["book"]
        authors = ", ".join(a["author"]["name"] for a in (book.get("contributions") or []))

        reads = ub.get("user_book_reads") or []
        latest = reads[-1] if reads else {}

        started = parse_iso_date(latest.get("started_at"))
        finished_at = parse_iso_date(latest.get("finished_at"))

        pages = book.get("pages") if isinstance(book.get("pages"), int) else None
        progress = latest.get("progress") or 0

        pct = None
        missing = False
        if pages and pages > 0:
            pct = int(progress / pages * 100)
        else:
            if ub.get("status_id") == 2:
                missing = True

        duration_days = None
        if started and finished_at:
            duration_days = (finished_at - started).days + 1

        entry = {
            "title": book.get("title"),
            "author": authors,
            "pages": pages,
            "cover": (book["image"]["url"] if book.get("image") else None),
            "rating_stars": ub.get("rating"),
            "hardcover_book_url": f"https://hardcover.app/books/{book['slug']}",
            "progress": int(progress),
            "pct": pct,
            "duration_days": duration_days,
            "missing": missing,
            "started_date": started,
            "finished_date": finished_at,
        }

        if ub.get("status_id") == 2:
            currently.append(entry)
        elif ub.get("status_id") == 3:
            finished.append(entry)

    totals = compute_totals(finished)
    books_per_year, books_per_year_max = compute_books_per_year(finished)
    timeline = compute_timeline(finished)

    # pace (year-to-date)
    year = datetime.now().year
    ytd_books = sum(1 for b in finished if b.get("finished_date") and b["finished_date"].year == year)
    ytd_pages = sum((b["pages"] or 0) for b in finished if b.get("finished_date") and b["finished_date"].year == year)
    day_of_year = datetime.now().timetuple().tm_yday
    pace_pages_per_day = round(ytd_pages / day_of_year, 1) if day_of_year else 0
    pace_books_per_month = round(ytd_books / max(1, datetime.now().month), 2)

    goals = me_raw.get("goals") or []
    goal_total = goals[0]["goal"] if goals else 0
    goal_progress = goals[0]["progress"] if goals else 0
    goal_pct = (goal_progress / goal_total * 100) if goal_total else 0

    env = Environment(loader=FileSystemLoader(TEMPLATES))
    tpl = env.get_template("reading.html")

    html = tpl.render(
        base_path=base_path,
        me={
            "name": me_raw.get("name"),
            "username": me_raw.get("username"),
            "avatar": me_raw["image"]["url"] if me_raw.get("image") else None,
            "profile_url": f"https://hardcover.app/@{me_raw.get('username')}",
        },
        stats={
            "year": year,
            "goal_total": goal_total,
            "goal_progress": goal_progress,
            "goal_pct": goal_pct,
            "avg_days": None,
            "median_days": None,
            "streak_monthly_current": 0,
            "streak_monthly_best": 0,
            "pace_pages_per_day": pace_pages_per_day,
            "pace_books_per_month": pace_books_per_month,
        },
        totals=totals,
        currently=currently,
        books_per_year=books_per_year,
        books_per_year_max=books_per_year_max,
        timeline=timeline,
        build={"stamp": utc_stamp()},
    )

    (DOCS / "reading").mkdir(parents=True, exist_ok=True)
    (DOCS / "reading" / "index.html").write_text(html, encoding="utf-8")

    (DOCS / "build.json").write_text(
        json.dumps(
            {
                "build": utc_stamp(),
                "base_path": base_path,
                "counts": {"currently": len(currently), "finished": len(finished)},
                "totals": totals,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    copy_static()
    print("✔ static page built")

if __name__ == "__main__":
    main()
