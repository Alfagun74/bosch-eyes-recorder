# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.3.0] - 2026-10-24

### Added

- **Recordings browser in the web UI**: `/recordings` lists finalized segments
  grouped by day, with an embedded player, continuous playback from a chosen
  segment, and download links.
- `/api/recordings` JSON endpoint for the recording list.
- `/media/<token>/<path>` streaming endpoint with HTTP Range support for
  seeking, protected by an unguessable token.

### Changed

- Version bumped to 1.3.0.
- **Docker Compose is now the primary documented installation method**;
  CapRover/GitHub deployment remains available as an alternative.
- Added `.env.example` and expanded the Compose file (pinned MediaMTX build
  argument, more environment defaults, log rotation).

## [1.2.0] - 2026-10-23

### Added

- Web UI on port 80 with a live HLS video player (video + audio) and status
  dashboard.
- Browser-based Bosch login from the web UI using PKCE; copy the callback URL
  back into the page to finish the login.
- Refresh-token fallback form in the UI.
- Optional HTTP Basic authentication for the UI via `WEB_PASSWORD`.
- HLS proxy with an unguessable path token; MediaMTX ports stay on loopback.
- Bundled hls.js player (Apache-2.0).
- GitHub Actions CI (lint, unit tests, Docker build) and CapRover deployment
  workflow.

### Changed

- Recording switched from an ffmpeg segment muxer to MediaMTX's built-in fMP4
  recorder for robustness and lower resource usage.
- Container default HTTP port is now 80.
- Configuration default `HEALTH_PORT` changed to 80.
- Removed the unused `SETUP_PASSWORD` setting; `WEB_PASSWORD` protects the UI.

### Fixed

- Malformed MKV segments from the previous ffmpeg-based recorder.
- Scheme-less camera URLs returned by the Bosch API are normalized.
- TLS trust for Bosch's private PKI (Video CA 2A) without disabling
  certificate verification.

## [1.1.0] - 2026-10-23

### Added

- MediaMTX-based recording, retention, credential refresh, watchdog.
- Dockerfile with pinned MediaMTX and multi-arch support.
- CapRover `captain-definition` and compose file.
- Initial end-to-end validation on a Bosch Eyes Gen1 camera.

## [1.0.0] - 2026-10-23

### Added

- Initial recorder prototype.
