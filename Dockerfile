# Sightline — one FastAPI process in one container.
#
# There is no broker, no worker and no second service: at one to sixteen courses the work is
# I/O-bound against a local model server and there is nothing to distribute (SPEC §04).
#
# Four stages. `deps` resolves the locked dependency set, `dev` adds the dev extras and the test
# suite (this is what scripts/dev.sh runs), and `runtime` is the shipped image — no uv, no build
# tools, no test dependencies, and a non-root user.
#
# No secret is ever baked into a layer or an ARG. Configuration arrives as environment at run
# time, and .dockerignore keeps .env and everything under the data root out of the build context.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.9.5
# The native text stack WeasyPrint renders the PDF report with (roadmap item 14): pango lays text
# out, harfbuzz shapes it and subsets the embedded fonts, fontconfig finds the vendored ones. Every
# stage that renders a report installs exactly this list. The versions come from the base image's
# Debian release, which floats with the python tag — so a rebuild can move the sample report's
# bytes with no code change (README, "The sample report").
ARG PDF_LIBS="libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libfontconfig1"

# ---------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS deps

ARG PDF_LIBS
RUN apt-get update \
 && apt-get install -y --no-install-recommends ${PDF_LIBS} \
 && rm -rf /var/lib/apt/lists/*

# uv comes from PyPI rather than a second registry: one source, no registry authentication, and
# the same behaviour on a developer machine as in CI. It stays in this stage; the runtime image
# has no build tooling in it.
ARG UV_VERSION
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, and only the files that decide them, so an application edit does not
# re-resolve the whole environment.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------------------------
FROM deps AS dev

# The dev extras and the suite. This stage exists so that the documented way to run the tests,
# the linter and the formatter is the same interpreter and the same dependency set CI uses.
RUN uv sync --frozen --extra dev
COPY tests ./tests
COPY docs/tokens.css ./docs/tokens.css
# The suite reads the detector inventory out of SPEC §05 — tests/test_sample_course.py checks that
# every planted defect names a detector the spec actually lists — so the dev stage needs the spec.
# The runtime image does not, and does not get it.
COPY docs/SPEC.md ./docs/SPEC.md
# The synthetic sample course is a test fixture as well as a shipped example, so the suite cannot
# run without it. It is also what `python -m sightline.acquire --bundle` reads, which is why the
# runtime stage below carries it too.
COPY examples ./examples
# The rubric importer is tested against the committed instruments themselves rather than against a
# copy of them, because the facts it has to survive are facts about those files: one of them does
# not open in openpyxl's default mode at all, one category skips an index, and the counts have to
# come out of the workbook rather than out of the spec. A fixture would agree with the importer
# instead of checking it. The workbooks are a blank template with no personal data (SPEC §11), and
# the runtime stage carries them too.
COPY rubrics ./rubrics
# The fonts the PDF report is set in, each beside its licence (assets/fonts/README.md).
COPY assets ./assets

# The committed sample report is read back by tests/test_sample_report.py, which re-renders it and
# compares the bytes. This stage is the environment the sample was made in, so the comparison is
# required to run here: set, the test fails where it would otherwise skip.
ENV PATH="/app/.venv/bin:$PATH" \
    SIGHTLINE_SAMPLE_REPORT_REQUIRED=1 \
    SIGHTLINE_DATA_DIR=/tmp/sightline-test-data \
    PYTHONDONTWRITEBYTECODE=1
CMD ["pytest", "-q"]

# ---------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="Sightline" \
      org.opencontainers.image.description="An automated first pass over an online course against a review rubric" \
      org.opencontainers.image.source="https://git.bitmapserv.org/bitmap/sightline" \
      org.opencontainers.image.licenses="MIT"

# uid is fixed so a bind-mounted data directory has predictable ownership.
RUN groupadd --system --gid 10001 sightline \
 && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/sightline sightline

# The PDF report renders here too: the export command runs in this image.
ARG PDF_LIBS
RUN apt-get update \
 && apt-get install -y --no-install-recommends ${PDF_LIBS} \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=deps --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root src ./src
COPY --chown=root:root rubrics ./rubrics
# The synthetic sample course: what a new user runs before they have a Canvas token.
COPY --chown=root:root examples ./examples
# The single source of colour. Served verbatim; never copied into the package.
COPY --chown=root:root docs/tokens.css ./docs/tokens.css
# The fonts the PDF report is set in, each beside its licence.
COPY --chown=root:root assets ./assets

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIGHTLINE_DATA_DIR=/data \
    SIGHTLINE_HOST=0.0.0.0 \
    SIGHTLINE_PORT=8000

# The data root is a mount point. Everything the app downloads or generates lands beneath it, and
# it is a volume so an image rebuild cannot wipe a half-finished batch.
RUN install -d -o sightline -g sightline -m 0750 /data
VOLUME ["/data"]

USER sightline
EXPOSE 8000

# Hits the app's own health endpoint, which reports the data root, the run store and the design
# tokens — a container that is up but cannot write run state is not healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "sightline.app:app", "--host", "0.0.0.0", "--port", "8000"]
