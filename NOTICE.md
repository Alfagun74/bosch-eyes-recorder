# Third-party notices

This project is unofficial and not affiliated with, endorsed by, or supported
by Bosch Security Systems or Bosch Smart Home.

## Bosch camera tool (MIT)

Parts of this project are based on or derived from the MIT-licensed project
[mosandlt/bosch-camera-tool](https://github.com/mosandlt/bosch-camera-tool)
(Copyright (c) 2026 Thomas Mosandl):

- `app/bosch_ca.py`: the PEM certificate of the Bosch "Video CA 2A", including
  the original comment, was taken unchanged from that project. The module was
  extended with an SSL adapter helper.
- The OAuth client constants (`CLIENT_ID`, `CLIENT_SECRET`, token endpoint,
  redirect URI, scopes) and the general PKCE browser-login pattern correspond
  to the publicly documented flow of the Bosch mobile app. These are public
  app constants, not customer secrets.
- The application logic (MediaMTX integration, recorder, retention, web UI,
  login flow) is original to this project.

The MIT license of the upstream project is compatible with this project's MIT
license; the upstream copyright notice is reproduced here.

## MediaMTX (MIT)

The Docker image downloads a pinned [MediaMTX](https://github.com/bluenviron/mediamtx)
release (`bluenviron/mediamtx`, MIT License) and installs the `mediamtx`
binary. MediaMTX handles RTSP pulling, fMP4 recording, and HLS generation.

## hls.js (Apache-2.0)

`app/static/hls.min.js` is hls.js 1.7.3
(https://github.com/video-dev/hls.js, Apache License 2.0). The license text is
included in `app/static/hls.js.LICENSE.txt`.

## Trademarks

Bosch, Bosch Smart Home, SingleKey ID, and Bosch Eyes are trademarks of their
respective owners. Use of these names is for identification only and does not
imply endorsement.

## No warranty

The software is provided "as is", without warranty of any kind. Use it at your
own risk and ensure that your use complies with applicable law, privacy rules,
and any terms of service that apply to you.

6. Das amd64-Image installiert zur Build-Zeit **jellyfin-ffmpeg8** aus dem
   Jellyfin-Repository (https://repo.jellyfin.org), einen FFmpeg-Build unter
   GPL-Lizenz (u. a. mit x264/x265/Intel-QSV/Opus). Das Binary wird nicht Teil
   des Quellcodes, sondern nur des gebauten Docker-Images; es gelten die
   Lizenzbedingungen des Jellyfin-FFmpeg-Pakets.
