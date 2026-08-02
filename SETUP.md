# Setup

1. **Upload these files** to the root of your `moohameedd/moohameedd` profile repo:
   - `main.py`
   - `requirements.txt`
   - `ascii-art.png`
   - `README.md`
   - `.github/workflows/main.yml`
   - `cache/` folder (can start empty — the script creates `cache/loc_cache.json` itself)

2. **Create a Personal Access Token** (this is what lets the Action read your
   private stats — the default `GITHUB_TOKEN` isn't enough for cross-repo LOC/commit queries):
   - Go to GitHub → Settings → Developer settings → Personal access tokens →
     Tokens (classic) → Generate new token
   - Scopes needed: `repo` and `read:user`
   - Copy the token (you only see it once)

3. **Add it as a repo secret**:
   - In your `moohameedd/moohameedd` repo → Settings → Secrets and variables →
     Actions → New repository secret
   - Name: `ACCESS_TOKEN`
   - Value: paste the token

4. **Run it**:
   - Go to the Actions tab → "Update GitHub stats SVG" → Run workflow
   - It'll compute your stats, render `stats.svg`, and commit it back to the repo
   - After that it re-runs automatically once a day (you can change the `cron`
     schedule in `.github/workflows/main.yml`)

5. Your README already just shows the graphic + your links, nothing else,
   exactly as you asked.

## Notes / things you may want to tweak in `main.py`
- The `stats` dict near the bottom hardcodes your OS/languages/contact info
  (since neofetch output and social handles aren't things the GitHub API
  knows about) — edit those strings any time your setup changes.
- "Lines of Code" is summed only across repos GitHub already associates with
  you (owned + contributed-to). It won't include repos you've deleted or
  private forks GitHub doesn't attribute to you.
- First run will be slower (no cache yet); after that, `cache/loc_cache.json`
  lets it skip repos that haven't changed.
