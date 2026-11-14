#!/bin/sh
# Print a short-lived GitHub installation token for a role's GitHub App.
#
# Usage: scripts/agent-token.sh <architect|developer|reviewer|qm|devops>
#
# Reads, outside the repository:
#   $NEXTOOMIO_APPS_DIR/app-ids       lines "<role>=<app id>"
#   $NEXTOOMIO_APPS_DIR/<role>.pem    the App's private key
# NEXTOOMIO_APPS_DIR defaults to ~/.config/nextoomio/apps.
#
# The token is valid for one hour and is written to standard output only.
# See docs/engineering/agent-team.md, OQ-12.
set -eu

role=${1:?usage: agent-token.sh <role>}
repo=${NEXTOOMIO_REPO:-nextoo-ai/nextoomio}
apps_dir=${NEXTOOMIO_APPS_DIR:-$HOME/.config/nextoomio/apps}
key=$apps_dir/$role.pem

case $role in
  architect|developer|reviewer|qm|devops) ;;
  *) echo "agent-token: unknown role '$role'" >&2; exit 2 ;;
esac
[ -r "$apps_dir/app-ids" ] || { echo "agent-token: $apps_dir/app-ids not readable" >&2; exit 1; }
[ -r "$key" ] || { echo "agent-token: $key not readable" >&2; exit 1; }
app_id=$(sed -n "s/^$role=//p" "$apps_dir/app-ids")
[ -n "$app_id" ] || { echo "agent-token: no App ID for role '$role' in app-ids" >&2; exit 1; }

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
now=$(date +%s)
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((now - 60))" "$((now + 300))" "$app_id" | b64url)
signature=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$key" -binary | b64url)
jwt=$header.$payload.$signature

api() {
  curl -fsS \
    -H "Authorization: Bearer $jwt" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$@"
}
field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

installation=$(api "https://api.github.com/repos/$repo/installation" | field id)
api -X POST "https://api.github.com/app/installations/$installation/access_tokens" | field token
