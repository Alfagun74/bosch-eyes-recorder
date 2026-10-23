# Security policy

## Supported versions

Security fixes are applied to the `main` branch and the latest release. Older
tags are not maintained.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead use the
repository's **Security → Report a vulnerability** (GitHub private security
advisory) feature.

Include:

- affected version/commit,
- a description of the issue and impact,
- reproduction steps or a proof of concept,
- any suggested fix.

You can expect an acknowledgement within a few days. Please allow time for a
fix and release before public disclosure.

## What counts as a security issue

- Authentication bypass of the web UI (when `WEB_PASSWORD` is set),
- exposure of the Bosch refresh/access token or temporary camera credentials
  through pages, logs, error messages, or API responses,
- remote code execution or path traversal in the HTTP server or HLS proxy,
- ways to access the camera stream without the configured credentials.

## Operational security checklist

- Set a strong `WEB_PASSWORD` (or enable CapRover HTTP Basic Auth in addition).
- Serve the app over HTTPS (CapRover does this automatically for custom
  domains).
- Restrict access to the CapRover dashboard and app configuration.
- Protect the `/data` volume: it contains the Bosch refresh token and temporary
  camera credentials (`/data/state/mediamtx.yml`).
- Keep the image updated (Dependabot is configured for Python, Actions, and
  Docker base images).
- Never post tokens, `/data` files, camera IPs, or account data in issues,
  screenshots, or logs.
- Remove a token from the volume/environment if you think it was exposed, and
  sign out the Bosch session.

## Threat model

The app is intended for a single trusted user on a private/self-hosted server.
It is not a multi-tenant system. Anyone who obtains the Bosch refresh token can
request camera credentials until the session is revoked. The camera stream is
not protected by Bosch's cloud in `LOCAL` mode; it is a direct LAN connection
using temporary credentials.
