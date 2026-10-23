# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.1] - 2026-10-24

### Fixed

- **Audio encoder error on camera segments without audio packets**
  (`Could not open encoder before EOF`, `Nothing was written into output file`).
  Segments are now probed for usable audio packets first; if there is no
  audio data, the transcode runs video-only. If the Opus encoder still fails,
  the worker retries without audio instead of dropping the whole segment.
- Mobile/responsive UI polish: two-row header with scrollable navigation,
  full-width page actions removed, tighter cards/filters/segment rows, timeline
  labels reduced on small screens, single status card spans full width.
- Empty recordings player now hides native controls until a segment is
  selected.

### Added

- `FFPROBE_BINARY` configuration (defaults to the bundled Jellyfin FFmpeg
  `ffprobe`).
- Tests for audio packet detection and video-only commands.

## [1.4.0] - 2026-10-24

### Added

- **Optional AV1/Opus hardware transcoding** with Intel QSV (`av1_qsv` +
  `libopus`) for finalized segments; new `.webm`/`.mkv`/`.mp4` outputs and a
  background worker that keeps the original until the transcode succeeds.
- **Modern responsive web UI**: dashboard with live player and status cards,
  login flow, dedicated system page, dark/light theme.
- **Recordings browser improvements**: day picker, time-of-day filters, day
  chips, 24-hour timeline, codec badges, "play from here" queue, download links,
  and shareable filter URLs.
- Compose override `docker-compose.qsv.yml` for Intel Arc GPU passthrough.
- The amd64 image bundles Jellyfin FFmpeg (QSV + libopus) and verifies the
  encoder at build time.
- UI screenshot and additional documentation.

### Fixed

- **Recordings playback did nothing** because the player JavaScript attached
  its click handlers before the segment buttons existed in the DOM. The UI now
  initializes after `DOMContentLoaded`.
- The recordings API and player now support `.mp4`, `.webm` and `.mkv` segments.

### Changed

- Version bumped to 1.4.0.
- Docker Compose remains the primary installation path; the QSV override is a
  documented add-on.

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
