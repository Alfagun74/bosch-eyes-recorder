# Contributing

Thanks for your interest! This project is small and pragmatic; focused,
well-tested changes are very welcome.

## Ground rules

- Be respectful. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- **Never commit secrets**: no refresh tokens, access tokens, camera IPs,
  account identifiers, or `/data` contents. Redact logs in issues/PRs.
- Keep the scope focused: recording, browser UI, login, retention, deployment.
  Large refactors or new dependencies need an issue first.
- By contributing you agree that your contribution is licensed under the
  project's MIT license.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt ruff

ruff check app tools tests
python -m unittest discover -s tests -v
python -m compileall -q app tools
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for running the app locally and
building the Docker image.

## Pull requests

1. Fork the repository and create a topic branch.
2. Add or update tests for your change.
3. Run linting and tests locally.
4. Update documentation if behavior or configuration changes.
5. Open a PR with a clear description of the problem and the solution.

## Commit messages

Use short imperative subjects, e.g.:

```text
Add optional WEB_PASSWORD basic auth
Fix HLS proxy content-type for audio playlists
```

## Reporting bugs

Use the GitHub issue templates. Include:

- app version / commit,
- container platform (CapRover/Docker) and architecture,
- relevant logs with secrets redacted,
- `/status` output with private fields redacted.

For security-sensitive reports follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.
