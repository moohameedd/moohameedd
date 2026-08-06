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
            return 0, 0
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

FONT      = "SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"
BG        = "#0a0a0a"     # same near-black as the ascii-art background
WHITE     = "#e8e8e8"     # primary text  — bright white
ORANGE    = "#d4845a"     # accent colour — sampled from the ASCII art warm tones
GRAY      = "#606060"     # dots / rules
GREEN     = "#98c379"     # loc ++
RED       = "#e06c75"     # loc --
LABEL_COL = 26            # number of dot-fill chars per row

# ── font sizes (bigger than before) ─────────────────────────────────────────
FS_HEADER = 26   # section headers  e.g. "moohameedd@github"
FS_RULE   = 24   # separator line   "------..."
FS_ROW    = 24   # data rows
LINE_H    = 36   # vertical spacing between lines (px)
CHAR_W    = 14.5 # approx advance width of the monospace font at FS_ROW


def dotted(label, value, value_color=ORANGE):
    """Return (svg-markup, plain-text-length) for one key....value row."""
    pad  = max(1, LABEL_COL - len(label))
    dots = "." * pad
    plain_len = len(label) + 1 + pad + 1 + len(value)
    markup = (
        f'<tspan fill="{WHITE}">{label}</tspan>'
        f'<tspan fill="{GRAY}"> {dots} </tspan>'
        f'<tspan fill="{value_color}">{value}</tspan>'
    )
    return markup, plain_len


def build_svg(stats, art_b64, art_w, art_h):
    # ── build line list (NO blank() calls → no empty gaps) ──────────────────
    lines = []   # each item: (kind, content, plain_len)

    def header(text):
        lines.append(("header", text, len(text)))

    def rule(n=44):
        lines.append(("rule", "-" * n, n))

    def row(label, value, color=ORANGE):
        markup, plain_len = dotted(label, value, color)
        lines.append(("row", markup, plain_len))

    # ── neofetch block ───────────────────────────────────────────────────────
    header(f"{USER_NAME}@github")
    rule()
    row("OS",     stats["os"])
    row("Host",   stats["host"])
    row("Kernel", stats["kernel"])
    row("Shell",  stats["shell"])
    row("DE",     stats["de"])
    row("Languages.Programming", stats["prog_langs"])
    row("Languages.Web",         stats["web_langs"])
    row("Languages.ML",          stats["ml_libs"])
    row("Tools.IDE",             stats["ide"])
    row("Tools.Design",          stats["design"])

    # ── Contact ──────────────────────────────────────────────────────────────
    header("Contact")
    rule()
    row("Email",      stats["email"])
    row("LinkedIn",   stats["linkedin"])
    row("YouTube",    stats["youtube"])
    row("LeetCode",   stats["leetcode"])
    row("Codeforces", stats["codeforces"])
    row("MonkeyType", stats["monkeytype"])

    # ── GitHub Stats ─────────────────────────────────────────────────────────
    header("GitHub Stats")
    rule()
    row("Repos",     f'{stats["repos"]} {{Contributed: {stats["contributed"]}}}')
    row("Stars",     str(stats["stars"]))
    row("Commits",   f'{stats["commits"]:,}')
    row("Followers", str(stats["followers"]))

    loc_plain = (
        f'{stats["loc_add"] - stats["loc_del"]:,} '
        f'({stats["loc_add"]:,}++, {stats["loc_del"]:,}--)'
    )
    loc_markup, loc_plain_len = dotted("Lines of Code", loc_plain)
    # colour the ++ and -- in green/red
    loc_markup = (
        loc_markup
        .replace(f'{stats["loc_add"]:,}++',
                 f'<tspan fill="{GREEN}">{stats["loc_add"]:,}++</tspan>')
        .replace(f'{stats["loc_del"]:,}--',
                 f'<tspan fill="{RED}">{stats["loc_del"]:,}--</tspan>')
    )
    lines.append(("row", loc_markup, loc_plain_len))

    # ── geometry ─────────────────────────────────────────────────────────────
    margin     = 32
    art_margin = 14

    text_h   = len(lines) * LINE_H
    canvas_h = text_h + margin * 2

    max_art_h  = canvas_h - art_margin * 2
    art_disp_h = max_art_h            # fill the full height
    art_disp_w = art_w * (art_disp_h / art_h)
    art_x = art_margin
    art_y = art_margin

    text_x    = art_x + art_disp_w + 50
    max_chars = max((pl for _, _, pl in lines), default=0)
    canvas_w  = text_x + max_chars * CHAR_W + margin

    # ── SVG text elements ────────────────────────────────────────────────────
    svg_lines = []
    y = margin + LINE_H - 6   # first baseline

    for kind, content, _ in lines:
        if kind == "header":
            svg_lines.append(
                f'<text x="{text_x:.0f}" y="{y}" '
                f'font-family="{FONT}" font-size="{FS_HEADER}" '
                f'font-weight="bold" fill="{WHITE}">{content}</text>'
            )
        elif kind == "rule":
            svg_lines.append(
                f'<text x="{text_x:.0f}" y="{y}" '
                f'font-family="{FONT}" font-size="{FS_RULE}" '
                f'fill="{GRAY}">{content}</text>'
            )
        else:  # row
            svg_lines.append(
                f'<text x="{text_x:.0f}" y="{y}" '
                f'font-family="{FONT}" font-size="{FS_ROW}">'
                f'{content}</text>'
            )
        y += LINE_H

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{canvas_w:.0f}" height="{canvas_h:.0f}">\n'
        f'  <rect width="100%" height="100%" fill="{BG}" rx="10"/>\n'
        f'  <image x="{art_x}" y="{art_y}" '
        f'width="{art_disp_w:.0f}" height="{art_disp_h:.0f}"\n'
        f'         href="data:image/png;base64,{art_b64}"/>\n'
        + "\n".join(svg_lines)
        + "\n</svg>"
    )
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
        "os":         "Ubuntu 24.04.4 LTS x86_64",
        "host":       "Thin GF63 12UCX",
        "kernel":     "7.0.0-28-generic",
        "shell":      "bash 5.2.21",
        "de":         "GNOME 46.0",
        "prog_langs": "Python, Java, C, JavaScript, PHP",
        "web_langs":  "HTML, CSS, MySQL",
        "ml_libs":    "PyTorch, NumPy, Pandas, scikit-learn",
        "ide":        "VS Code, Android Studio",
        "design":     "Canva",
        "email":      "hama.ferchichi321@gmail.com",
        "linkedin":   "mohamed-ferchichi-5626b3330",
        "youtube":    "@moohameedd-y3r",
        "leetcode":   "moohameedd",
        "codeforces": "moohameedd",
        "monkeytype": "mohamedferchichi",
        "repos":      repo_count,
        "contributed":contributed_count,
        "stars":      stars,
        "commits":    commits,
        "followers":  followers,
        "loc_add":    loc_add,
        "loc_del":    loc_del,
    }

    with open("ascii-art.png", "rb") as f:
        art_b64 = base64.b64encode(f.read()).decode()
    art_w, art_h = 1152, 1536   # exact dimensions of ascii-art.png

    svg = build_svg(stats, art_b64, art_w, art_h)
    with open("stats.svg", "w") as f:
        f.write(svg)
    print("stats.svg written.")


if __name__ == "__main__":
    main()
