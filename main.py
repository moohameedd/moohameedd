"""
Generates stats.svg: an ASCII-art + neofetch-style header for a GitHub
profile README, with live repo/commit/star/follower/line-count stats
pulled from the GitHub GraphQL API.

Required environment variables:
    USER_NAME     - the GitHub login to fetch stats for (e.g. "moohameedd")
    ACCESS_TOKEN  - a GitHub PAT with `read:user` and `repo` scopes
"""

import os
import base64
import json
import datetime
import requests

USER_NAME = os.environ["USER_NAME"]
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
API_URL = "https://api.github.com/graphql"
HEADERS = {"Authorization": f"bearer {ACCESS_TOKEN}"}
CACHE_PATH = "cache/loc_cache.json"

# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------


def gql(query, variables=None):
    resp = requests.post(
        API_URL, headers=HEADERS, json={"query": query, "variables": variables or {}}
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def get_user_info(login):
    query = """
    query($login: String!) {
      user(login: $login) {
        id
        createdAt
        followers { totalCount }
      }
    }
    """
    d = gql(query, {"login": login})["user"]
    return d["id"], d["createdAt"], d["followers"]["totalCount"]


def get_owned_repos(login):
    query = """
    query($login: String!, $after: String) {
      user(login: $login) {
        repositories(first: 100, after: $after, ownerAffiliations: [OWNER], isFork: false) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { nameWithOwner stargazerCount }
        }
      }
    }
    """
    repos, after, stars = [], None, 0
    while True:
        d = gql(query, {"login": login, "after": after})["user"]["repositories"]
        for n in d["nodes"]:
            repos.append(n["nameWithOwner"])
            stars += n["stargazerCount"]
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    return repos, d["totalCount"], stars


def get_contributed_repos(login):
    query = """
    query($login: String!, $after: String) {
      user(login: $login) {
        repositoriesContributedTo(first: 100, after: $after, includeUserRepositories: false, contributionTypes: [COMMIT]) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { nameWithOwner }
        }
      }
    }
    """
    repos, after = [], None
    while True:
        d = gql(query, {"login": login, "after": after})["user"]["repositoriesContributedTo"]
        for n in d["nodes"]:
            repos.append(n["nameWithOwner"])
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    return repos, d["totalCount"]


def get_commit_count(login, created_at):
    """Sum contributionsCollection.totalCommitContributions in 1-year windows
    from account creation to now (GitHub only allows a 1-year span per query)."""
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
        }
      }
    }
    """
    start = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    total = 0
    cursor = start
    while cursor < now:
        window_end = min(cursor + datetime.timedelta(days=365), now)
        d = gql(query, {
            "login": login,
            "from": cursor.isoformat(),
            "to": window_end.isoformat(),
        })["user"]["contributionsCollection"]
        total += d["totalCommitContributions"] + d["restrictedContributionsCount"]
        cursor = window_end
    return total


def get_loc_for_repo(owner, name, user_id, cache):
    """Sum additions/deletions on the default branch, authored by user_id.
    Uses a local cache keyed on the repo's latest commit oid to skip repos
    that haven't changed since the last run."""
    key = f"{owner}/{name}"
    query_head = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef { target { ... on Commit { oid } } }
      }
    }
    """
    try:
        d = gql(query_head, {"owner": owner, "name": name})["repository"]
        ref = d["defaultBranchRef"]
        if ref is None:
            return 0, 0  # empty repo, no default branch
        head_oid = ref["target"]["oid"]
    except Exception:
        return 0, 0

    if key in cache and cache[key]["oid"] == head_oid:
        return cache[key]["additions"], cache[key]["deletions"]

    query_history = """
    query($owner: String!, $name: String!, $after: String, $id: ID!) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $after, author: { id: $id }) {
                totalCount
                pageInfo { hasNextPage endCursor }
                nodes { additions deletions }
              }
            }
          }
        }
      }
    }
    """
    additions = deletions = 0
    after = None
    try:
        while True:
            d = gql(query_history, {
                "owner": owner, "name": name, "after": after, "id": user_id,
            })["repository"]["defaultBranchRef"]["target"]["history"]
            for c in d["nodes"]:
                additions += c["additions"]
                deletions += c["deletions"]
            if not d["pageInfo"]["hasNextPage"]:
                break
            after = d["pageInfo"]["endCursor"]
    except Exception:
        pass

    cache[key] = {"oid": head_oid, "additions": additions, "deletions": deletions}
    return additions, deletions


def get_total_loc(repos, user_id):
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            cache = json.load(f)
    else:
        cache = {}

    total_add = total_del = 0
    for full_name in repos:
        owner, name = full_name.split("/", 1)
        a, d = get_loc_for_repo(owner, name, user_id, cache)
        total_add += a
        total_del += d

    os.makedirs("cache", exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)
    return total_add, total_del


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

FONT = "SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"
BG = "#0a0a0f"        # matches the ascii-art background exactly, so the two blend
ACCENT = "#ff2b9d"     # sampled from the ascii art's dominant pink
WHITE = "#e6e6e6"
GRAY = "#5c6370"
GREEN = "#98c379"
RED = "#e06c75"
LABEL_COL = 34   # dotted line target column (characters)


def dotted(label, value, color=WHITE):
    pad = max(1, LABEL_COL - len(label))
    dots = "." * pad
    return (
        f'<tspan fill="{ACCENT}">{label}</tspan>'
        f'<tspan fill="{GRAY}"> {dots} </tspan>'
        f'<tspan fill="{color}">{value}</tspan>'
    )


def build_svg(stats, art_b64, art_w, art_h):
    line_h = 22
    lines = []

    def header(text):
        lines.append(("header", text))

    def row(label, value, color=WHITE):
        lines.append(("row", dotted(label, value, color)))

    def blank():
        lines.append(("blank", ""))

    header(f"{USER_NAME}@github")
    lines.append(("rule", "-" * 44))
    row("OS", stats["os"])
    row("Host", stats["host"])
    row("Kernel", stats["kernel"])
    row("Shell", stats["shell"])
    row("DE", stats["de"])
    blank()
    row("Languages.Programming", stats["prog_langs"])
    row("Languages.Web", stats["web_langs"])
    row("Languages.ML", stats["ml_libs"])
    blank()
    row("Tools.IDE", stats["ide"])
    row("Tools.Design", stats["design"])
    blank()
    header("Contact")
    lines.append(("rule", "-" * 44))
    row("Email", stats["email"])
    row("LinkedIn", stats["linkedin"])
    row("YouTube", stats["youtube"])
    row("LeetCode", stats["leetcode"])
    row("Codeforces", stats["codeforces"])
    row("MonkeyType", stats["monkeytype"])
    blank()
    header("GitHub Stats")
    lines.append(("rule", "-" * 44))
    row("Repos", f'{stats["repos"]} {{Contributed: {stats["contributed"]}}}')
    row("Stars", str(stats["stars"]))
    row("Commits", f'{stats["commits"]:,}')
    row("Followers", str(stats["followers"]))
    loc_str = (
        f'{stats["loc_add"] - stats["loc_del"]:,} '
        f'(<tspan fill="{GREEN}">{stats["loc_add"]:,}++</tspan>, '
        f'<tspan fill="{RED}">{stats["loc_del"]:,}--</tspan>)'
    )
    lines.append(("row", dotted("Lines of Code", "") + loc_str))

    margin = 30
    text_h = len(lines) * line_h
    canvas_h = text_h + margin * 2

    # Scale the art to the full content height (not a fixed width), so its
    # proportions match the height of the text column instead of leaving
    # dead space above/below it.
    art_disp_h = canvas_h - margin * 2
    art_disp_w = art_w * (art_disp_h / art_h)
    art_x = margin
    art_y = margin

    text_x = art_x + art_disp_w + 50
    canvas_w = text_x + 560

    svg_lines = []
    y = margin + 16
    for kind, content in lines:
        if kind == "blank":
            y += line_h
            continue
        if kind == "header":
            svg_lines.append(
                f'<text x="{text_x:.0f}" y="{y}" font-family="{FONT}" '
                f'font-size="16" font-weight="bold" fill="{WHITE}">{content}</text>'
            )
        elif kind == "rule":
            svg_lines.append(
                f'<text x="{text_x:.0f}" y="{y}" font-family="{FONT}" '
                f'font-size="16" fill="{GRAY}">{content}</text>'
            )
        else:
            svg_lines.append(
                f'<text x="{text_x:.0f}" y="{y}" font-family="{FONT}" '
                f'font-size="15">{content}</text>'
            )
        y += line_h

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w:.0f}" height="{canvas_h:.0f}">
  <rect width="100%" height="100%" fill="{BG}" rx="10"/>
  <image x="{art_x}" y="{art_y}" width="{art_disp_w:.0f}" height="{art_disp_h:.0f}"
         href="data:image/png;base64,{art_b64}"/>
  {"".join(svg_lines)}
</svg>'''
    return svg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    user_id, created_at, followers = get_user_info(USER_NAME)
    owned_repos, repo_count, stars = get_owned_repos(USER_NAME)
    contributed_repos, contributed_count = get_contributed_repos(USER_NAME)
    commits = get_commit_count(USER_NAME, created_at)

    all_repos = sorted(set(owned_repos) | set(contributed_repos))
    loc_add, loc_del = get_total_loc(all_repos, user_id)

    stats = {
        "os": "Ubuntu 24.04.4 LTS x86_64",
        "host": "Thin GF63 12UCX",
        "kernel": "7.0.0-28-generic",
        "shell": "bash 5.2.21",
        "de": "GNOME 46.0",
        "prog_langs": "Python, Java, C, JavaScript, PHP",
        "web_langs": "HTML, CSS, MySQL",
        "ml_libs": "PyTorch, NumPy, Pandas, scikit-learn",
        "ide": "VS Code, Android Studio",
        "design": "Canva",
        "email": "hama.ferchichi321@gmail.com",
        "linkedin": "mohamed-ferchichi-5626b3330",
        "youtube": "@moohameedd-y3r",
        "leetcode": "moohameedd",
        "codeforces": "moohameedd",
        "monkeytype": "mohamedferchichi",
        "repos": repo_count,
        "contributed": contributed_count,
        "stars": stars,
        "commits": commits,
        "followers": followers,
        "loc_add": loc_add,
        "loc_del": loc_del,
    }

    with open("ascii-art.png", "rb") as f:
        art_b64 = base64.b64encode(f.read()).decode()
    art_w, art_h = 1152, 1536

    svg = build_svg(stats, art_b64, art_w, art_h)
    with open("stats.svg", "w") as f:
        f.write(svg)


if __name__ == "__main__":
    main()
