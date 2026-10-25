#!/usr/bin/env bash
# One-command path from this folder to a live Vercel deployment.
#   GH_USER=yourhandle REPO=elysium-qa ./push.sh
set -euo pipefail

GH_USER="${GH_USER:-}"
REPO="${REPO:-elysium-qa}"

if [ -z "$GH_USER" ]; then
  echo "usage: GH_USER=yourhandle REPO=elysium-qa ./push.sh"
  echo "(needs the GitHub CLI: https://cli.github.com — or create the repo in the browser)"
  exit 1
fi

git init -b main 2>/dev/null || true
git add -A
git commit -m "Elysium launch readiness index: QA harness + dashboard" || echo "nothing to commit"

if command -v gh >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --remote=origin --push
  echo
  echo "repo: https://github.com/$GH_USER/$REPO"
else
  echo "gh not installed. Create the repo, then:"
  echo "  git remote add origin git@github.com:$GH_USER/$REPO.git"
  echo "  git push -u origin main"
fi

cat <<'MSG'

next: vercel
  1. vercel.com/new  ->  import the repo
  2. framework preset: Other      (no build step; index.html is committed)
  3. build command: (leave empty)   output directory: (leave empty)
  4. Deploy

You get the dashboard at https://<project>.vercel.app
Optional live re-run: https://<project>.vercel.app/api/refresh?format=html
MSG
