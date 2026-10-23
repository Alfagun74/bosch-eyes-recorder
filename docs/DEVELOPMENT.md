# Development

## Requirements

- Python 3.11 or 3.12
- `git`, `docker` (optional, for image builds)
- A MediaMTX binary matching your OS for local end-to-end runs:
  <https://github.com/bluenviron/mediamtx/releases>
- A Bosch Eyes Gen1 camera and account for integration testing (optional; unit
  tests do not need them)

## Setup

```bash
git clone https://github.com/Alfagun74/bosch-eyes-recorder.git
cd bosch-eyes-recorder
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install ruff
```

## Tests and linting

```bash
ruff check app tools tests
python -m unittest discover -s tests -v
python -m compileall -q app tools
```

The test suite uses only the standard library plus `requests`. It does not
contact Bosch or a camera.

## Run locally

```bash
export MEDIAMTX_BINARY=/path/to/mediamtx
export DATA_DIR=./data
export HEALTH_PORT=8080
export WEB_PASSWORD=dev-password      # optional but recommended
python -m app.main
```

Then open <http://127.0.0.1:8080> and log in.

A local run creates `./data/recordings/...` and `./data/state/...`. Both are
git-ignored.

## Docker build

```bash
docker build -t bosch-eyes-recorder:dev .
docker run --rm -p 8080:80 -v "$PWD/data:/data" \
  -e WEB_PASSWORD=dev-password \
  bosch-eyes-recorder:dev
```

The build downloads a pinned MediaMTX release for the target architecture
(`amd64` or `arm64`).

## Project structure

```text
app/
  __init__.py
  main.py            orchestration, worker, watchdog
  config.py          environment configuration
  auth.py            refresh-token store and OAuth refresh
  oauth.py           PKCE browser login flow
  cloud.py           Bosch cloud API client
  bosch_ca.py        Bosch Video CA trust store
  mediamtx.py        MediaMTX config + supervisor + status
  retention.py       rolling deletion
  status_http.py     web UI, HLS proxy, health/status
  static/            bundled hls.js (Apache-2.0)
tests/               unit tests
tools/               optional refresh-token helper
docs/                architecture/login/troubleshooting/dev docs
```

## Adding an endpoint

1. Extend `app/status_http.py` (route in `do_GET`/`do_POST`).
2. Keep secrets out of HTML/JSON responses.
3. Add a unit test if the logic is testable without a camera.
4. Update `docs/` and the endpoint table in the README.

## CapRover deployment workflow

`.github/workflows/deploy-caprover.yml` uses
[`caprover/deploy-from-github@v2`](https://github.com/caprover/deploy-from-github)
and the repository secrets `CAPROVER_SERVER`, `CAPROVER_APP_NAME`, and
`CAPROVER_APP_TOKEN`. It packages the committed repository and lets CapRover
build the image with `captain-definition`.

For a prebuilt-image deployment (not included by default, but recommended for
heavier apps), build and push the image to a registry in CI and pass its URL to
the action's `image` input; CapRover then only pulls the image. For this small
image the source build is usually fine.

## Releasing

1. Update the version in `app/main.py`, `Dockerfile` labels, and
   `CHANGELOG.md`.
2. Commit, tag (`git tag vX.Y.Z`), push the tag.
3. Optionally create a GitHub release from the tag.
