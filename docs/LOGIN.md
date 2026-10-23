# Login details

## Why a browser login is required

Bosch's OAuth client used by the Smart Home app (`oss_residential_app`) does
not permit the resource-owner password grant. Attempting a direct
username/password token request returns:

```json
{"error":"unauthorized_client","error_description":"Client not allowed for direct access grants"}
```

The official flow is **SingleKey ID** in a browser. It may involve captcha, MFA,
passkeys, or other interactive steps. Automating that with a headless browser
would be fragile and would also require storing the account password. This
project deliberately does not do that.

Instead, the app uses a **refresh token** created once by a real browser login.
Bosch supports `offline_access`; the refresh token can be renewed indefinitely
as long as the session stays valid and is used consistently.

## The PKCE callback problem

The Bosch OAuth client registers the redirect URI:

```text
http://localhost:8321/callback
```

A container on a remote server cannot receive a request to the user's
`localhost:8321`. Therefore the app uses this flow:

1. The app generates a PKCE `code_verifier`, its SHA-256 `code_challenge`, and a
   random `state`; it stores them server-side with a login id (30 min TTL).
2. The browser is redirected to the Bosch authorization endpoint with
   `redirect_uri=http://localhost:8321/callback`.
3. After login, the browser is redirected to the registered localhost URI. It
   cannot connect, but the address bar contains the authorization `code` and
   `state`.
4. The user copies the full URL and pastes it into `/login`.
5. The app verifies `state`, extracts `code`, and exchanges it together with
   the stored `code_verifier` at Bosch's token endpoint. PKCE protects the code
   from being useful to a third party that only sees the URL.
6. The token response (access + refresh token) is stored atomically in
   `/data/state/bosch_token.json` with mode 600.

## Token rotation and concurrency

Bosch/Keycloak rotates refresh tokens: each successful refresh returns a new
refresh token and invalidates the previous one. Reusing an old refresh token
can trigger reuse detection and revoke the whole session family. Consequences:

- The app writes the new refresh token **before** using the new access token.
- Only one process may use a token chain. Do not run the same token in two
  containers, two app instances, or alongside a different tool (for example a
  PC relay that uses the same login).
- For multiple cameras/instances, create a separate browser login for each
  instance.

## Alternative: token via environment variable

If you prefer not to use the UI login, create a refresh token with the included
helper on a workstation:

```bash
pip install requests
python tools/export_refresh_token.py
# writes bosch-refresh-token.json (mode 600) in the current directory
```

Paste the `refresh_token` value (or the whole JSON) into the app's `/login`
page (token fallback form) or set it as the `BOSCH_REFRESH_TOKEN` environment
variable. The app validates the token immediately and refuses invalid input
without overwriting a previously working token.

## Revoking access

To revoke the integration:

1. Stop the container.
2. Remove the app's stored token from the volume:
   `docker run --rm -v <volume>:/data alpine rm -f /data/state/bosch_token.json`
   (or delete the volume).
3. In your Bosch account, sign out the sessions associated with the app /
   change the account password if you want to invalidate all refresh tokens.
4. Remove the `BOSCH_REFRESH_TOKEN` environment variable.

## Security notes

- A refresh token is a bearer credential. Anyone with it can request camera
  credentials as long as Bosch allows the session. Protect the volume and never
  publish the token.
- The app never asks for or stores the account password.
- The OAuth client id/secret embedded in the app are public constants of the
  official mobile app, not customer secrets.
