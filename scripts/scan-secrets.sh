#!/bin/sh
# Fail on anything that must never be published from this repository.
#
# This is a PUBLIC repository, so the bar is higher than usual and the job is not decoration.
# Three checks, each of which reports what it examined:
#
#   1. gitleaks over the full commit history — a secret removed in a later commit is still public.
#   2. A scan of the working tree for token shapes.
#   3. A scan for the configured Canvas hostname, whose regular expression arrives in
#      FORBIDDEN_HOST_RE. The real hostname lives in that variable and nowhere else in this repo.
#
# A check that cannot run says so and fails, rather than passing quietly. The one exception is
# check 3 with FORBIDDEN_HOST_RE unset, which is a deployment that has not configured it; that is
# reported as SKIPPED and does not fail, because there is nothing to look for.
#
# If this script flags something, the fix is to REMOVE it and rotate the credential. Never add an
# ignore rule.
set -u

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root" || exit 1

status=0
note() { printf '%s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; status=1; }
pass() { printf 'ok    %s\n' "$*"; }

# --- 1. gitleaks over the full history --------------------------------------------------------
if command -v gitleaks >/dev/null 2>&1; then
    if gitleaks git --redact --no-banner . >/tmp/sightline-gitleaks.log 2>&1; then
        pass "gitleaks: no findings in history"
    else
        fail "gitleaks: findings in history (values redacted below)"
        cat /tmp/sightline-gitleaks.log
    fi
else
    fail "gitleaks: not installed, so history was NOT scanned"
fi

# --- 2. token shapes in the working tree ------------------------------------------------------
# A Canvas personal access token is a short numeric prefix, a tilde, then a long alphanumeric
# body. The placeholder in .env.example does not match: it has underscores and is too short.
token_hits=$(git grep -nIE \
    '[0-9]{3,6}~[A-Za-z0-9]{20,}|(Bearer|Authorization:[[:space:]]*token)[[:space:]]+[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9_-]{20,}' \
    -- . ':(exclude)scripts/scan-secrets.sh' ':(exclude).gitleaks.toml' 2>/dev/null | cut -d: -f1,2)
if [ -n "$token_hits" ]; then
    fail "token shapes found (locations only; the matched text is not printed):"
    printf '%s\n' "$token_hits"
else
    pass "token shapes: none in the tracked tree"
fi

# --- 3. the configured Canvas hostname --------------------------------------------------------
if [ -z "${FORBIDDEN_HOST_RE:-}" ]; then
    note "SKIP  hostname scan: FORBIDDEN_HOST_RE is not set, so there is nothing to look for."
    note "      Set it to a regular expression matching hostnames that must never appear here."
else
    host_hits=$(git grep -nIiE "$FORBIDDEN_HOST_RE" -- . 2>/dev/null | cut -d: -f1,2)
    if [ -n "$host_hits" ]; then
        fail "forbidden hostname found (locations only; the matched text is not printed):"
        printf '%s\n' "$host_hits"
    else
        pass "hostname scan: no forbidden host in the tracked tree"
    fi
fi

exit "$status"
