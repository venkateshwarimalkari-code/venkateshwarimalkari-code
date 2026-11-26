#!/usr/bin/env python3
"""Fetch contribution calendar + public repos for the profile cards.

Writes data/github.json, which render_cards.py reads. Run by the daily
GitHub Action; needs a token in GH_TOKEN (the workflow's GITHUB_TOKEN is
enough for public data; a classic PAT with read:user also counts private
contributions if you've turned that on in your profile settings).

    GH_TOKEN=... python scripts/fetch_github_data.py [login]
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGIN = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GH_LOGIN")
         or os.environ.get("GITHUB_REPOSITORY_OWNER") or "venkateshwarimalkari-code")

QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    pinnedItems(first: 6, types: REPOSITORY) {
      nodes { ... on Repository { name } }
    }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        name description url stargazerCount forkCount isFork pushedAt
        primaryLanguage { name color }
      }
    }
  }
}
"""


def main() -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GH_TOKEN is not set")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": LOGIN}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    u = payload["data"]["user"]
    cal = u["contributionsCollection"]["contributionCalendar"]
    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "login": u["login"],
        "name": u["name"],
        "calendar": {
            "total": cal["totalContributions"],
            "days": [{"date": d["date"], "count": d["contributionCount"]}
                     for w in cal["weeks"] for d in w["contributionDays"]],
        },
        "pinned": [n["name"] for n in u["pinnedItems"]["nodes"] if n],
        "repos": [{
            "name": r["name"], "description": r["description"], "url": r["url"],
            "stars": r["stargazerCount"], "forks": r["forkCount"], "is_fork": r["isFork"],
            "pushed_at": r["pushedAt"],
            "language": (r["primaryLanguage"] or {}).get("name"),
            "language_color": (r["primaryLanguage"] or {}).get("color"),
        } for r in u["repositories"]["nodes"]],
    }
    path = ROOT / "data" / "github.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {path.relative_to(ROOT)}: {cal['totalContributions']} contributions, "
          f"{len(out['repos'])} repos")


if __name__ == "__main__":
    main()
