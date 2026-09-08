#!/bin/sh
# Run a command inside the image's `dev` stage — the documented way to run the tests, the linter
# and the formatter.
#
# POSIX sh, not bash: this has to work in whatever shell is nearest, and a bashism here would only
# be found by somebody running the documented line on a machine that has no bash.
#
# Each task is a SEPARATE invocation on purpose. Chaining `ruff && black && echo OK` has hidden a
# black failure before now: the exit code that matters is the last one, and the `echo` masks it.
#
#   ./scripts/dev.sh pytest -q
#   ./scripts/dev.sh ruff check .
#   ./scripts/dev.sh black --check .
set -eu

if [ "$#" -eq 0 ]; then
    echo "usage: $0 COMMAND [ARGS...]   e.g. $0 pytest -q" >&2
    exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${SIGHTLINE_DEV_IMAGE:-sightline:dev}

# Build output goes to stderr so that a command whose stdout is piped stays parseable.
docker build --target dev -t "$image" "$repo_root" >&2

# --rm, and no host mounts: the image carries the source, so a stale container cannot linger and a
# host file cannot leak into the run.
exec docker run --rm -t "$image" "$@"
