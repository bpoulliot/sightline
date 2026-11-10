#!/bin/sh
# Start IMAGE as container NAME and wait for it to answer its own health endpoint.
#
#   ./scripts/image-healthcheck.sh IMAGE NAME
#
# The one health check for a built `runtime` image: CI's `image` job runs it on every PR, and the
# release workflow runs it on the image it is about to publish (roadmap item 49). An image that
# builds but cannot serve /healthz has not been tested. The container is removed on the way out,
# healthy or not; exit 1 if it never became healthy.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 IMAGE NAME" >&2
    exit 2
fi
image=$1
name=$2

docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" "$image"
ok=0
for _ in $(seq 1 30); do
    if docker exec "$name" python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)" 2>/dev/null; then
        ok=1
        break
    fi
    sleep 2
done
docker logs "$name" || true
docker exec "$name" python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read().decode())" || true
docker rm -f "$name" >/dev/null 2>&1 || true
if [ "$ok" -ne 1 ]; then
    echo "the image built but never became healthy" >&2
    exit 1
fi
echo "healthy: $image"
