"""
Authenticated access to the Sentinel camera grid.

Access model (from the logged-in Integrator's Guide)
----------------------------------------------------
Two different hosts, and this is the part that catches people out:

  HLS   https://cctv.corp8.cloud/<id>/index.m3u8
        CDN host, behind the access password. Works on ANY network.

  RTSP  rtsp://<email>:<password>@103.250.160.189:8554/stream/<id>
  WHEP  http://<email>:<password>@103.250.160.189:8889/stream/<id>/whep
        A CDN cannot proxy TCP/UDP media, so these are served DIRECTLY on the
        public static IP (or stream.corp8.cloud). Gateway ports 8554/TCP,
        8889/TCP, 8189/UDP must be open outbound on your network.

Note the asymmetry in the paths: HLS is  /<id>/index.m3u8  with no prefix,
RTSP and WHEP are  /stream/<id>  with one. Easy to get wrong.

Credentials are embedded in the RTSP/WHEP URL and the '@' in your email MUST be
percent-encoded as %40. Only emails on the approved access list can connect.

Catalogue
---------
    https://cctv.corp8.cloud/cameras.json        <- the real one
Camera ids run cam01 ... cam30. Read the catalogue; never hard-code ids.

Credentials come from the environment — never commit them:

    # PowerShell
    $env:SENTINEL_EMAIL="you@example.com"; $env:SENTINEL_PASSWORD="XXXX-XXXX-XXXX"
    # bash
    export SENTINEL_EMAIL=you@example.com SENTINEL_PASSWORD=XXXX-XXXX-XXXX

Usage
-----
    from sentinel_auth import SentinelSession

    with SentinelSession() as s:
        cams = s.catalogue()
        print(s.transport_report())
        url = s.stream_url("cam04")        # picks RTSP or HLS automatically
"""

from __future__ import annotations

import logging
import os
import re
import socket
from typing import Any, Literal
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger("sentinel.auth")

CDN_HOST = "https://cctv.corp8.cloud"
MEDIA_HOST = "103.250.160.189"          # or stream.corp8.cloud
RTSP_PORT = 8554
WHEP_PORT = 8889
WEBRTC_UDP_PORT = 8189

LOGIN_PATH = "/auth/login"
CATALOGUE_PATHS = ("/cameras.json", "/api/ingest")

LOGIN_MARKERS = ("Sign in · Sentinel", "Access the Camera Grid", 'name="password"')

Transport = Literal["rtsp", "hls"]

_CRED_RE = re.compile(r"(?<=//)[^/@\s]+:[^/@\s]+(?=@)")


def redact(text: str) -> str:
    """Strip embedded credentials before anything reaches a log or a bug report.

    RTSP URLs carry your email and access password in plaintext. They leak into
    log files, `ps` output, exception traces and screen recordings. Redact on
    the way out, always.
    """
    return _CRED_RE.sub("***:***", str(text))


class NotAuthenticated(RuntimeError):
    """The grid served the login page instead of the requested resource."""


class WatchQuotaExceeded(RuntimeError):
    """The account's watch-time quota is spent. Every endpoint answers HTTP 403
    text/plain "watch time limit reached — please wait for your cooldown, then
    watch again", and RTSP DESCRIBE answers 401 until the cooldown ends.
    Credentials are fine — do NOT retry in a loop."""


QUOTA_MARKER = "watch time limit"


def _check_quota(resp: requests.Response) -> None:
    if resp.status_code == 403 and QUOTA_MARKER in resp.content[:2000].decode("utf-8", "replace"):
        raise WatchQuotaExceeded(
            "grid watch-time quota spent (measured ~29 min of stream time on 2026-09-19); "
            "wait for the cooldown — credentials are not the problem"
        )


def _is_login_page(resp: requests.Response) -> bool:
    """True if the grid answered with its login page instead of the resource.

    Unauthenticated requests 302 to /auth/login and end as HTTP 200, so the
    status code says nothing. The page is sent as bare `text/html` with no
    charset, so requests decodes it as ISO-8859-1 and mangles the '·' in the
    title — decode as UTF-8 ourselves. The form markers sit ~7.7 KB in, past
    any short prefix window, so scan the whole (small) body.
    """
    if resp.history and urlparse(resp.url).path == LOGIN_PATH:
        return True
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype or "mpegurl" in ctype.lower():
        return False
    body = resp.content[:65536].decode("utf-8", errors="replace")
    return any(m in body for m in LOGIN_MARKERS)


def _load_dotenv(path: str | None = None) -> None:
    """Fill SENTINEL_* from a gitignored .env beside this file, if present.

    Real environment variables always win. Values are never logged.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.removeprefix("export ").partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key.startswith("SENTINEL_") and not os.environ.get(key):
            os.environ[key] = val


class SentinelSession:
    def __init__(
        self,
        cdn_host: str = CDN_HOST,
        *,
        media_host: str = MEDIA_HOST,
        email: str | None = None,
        password: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.cdn_host = cdn_host.rstrip("/")
        self.media_host = media_host
        self.timeout = timeout
        _load_dotenv()
        self.email = email or os.environ.get("SENTINEL_EMAIL", "")
        self._password = password or os.environ.get("SENTINEL_PASSWORD", "")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "SentinelIntegrator/0.2 (hackathon client)"
        self._logged_in = False
        self._transport: Transport | None = None

    def __enter__(self) -> "SentinelSession":
        self.login()
        return self

    def __exit__(self, *exc: object) -> None:
        self.session.close()

    # -- auth -------------------------------------------------------------
    def login(self) -> None:
        if not self.email or not self._password:
            raise NotAuthenticated("set SENTINEL_EMAIL and SENTINEL_PASSWORD in the environment")
        url = self.cdn_host + LOGIN_PATH
        self.session.get(url, timeout=self.timeout)          # prime cookies
        resp = self.session.post(
            url,
            data={"email": self.email, "password": self._password},
            timeout=self.timeout,
            allow_redirects=True,
        )
        _check_quota(resp)
        if _is_login_page(resp):
            raise NotAuthenticated(
                "login rejected — use the access password issued for the grid "
                "(format XXXX-XXXX-XXXX), and check your email is on the approved access list"
            )
        self._logged_in = True
        log.info("authenticated to %s", self.cdn_host)

    @property
    def cookie_header(self) -> str:
        """Cookie string for handing to FFmpeg/OpenCV when pulling HLS."""
        return "; ".join(f"{c.name}={c.value}" for c in self.session.cookies)

    # -- catalogue --------------------------------------------------------
    def catalogue(self, *, retries: int = 2) -> list[dict[str, Any]]:
        if not self._logged_in:
            self.login()
        last_err: str = ""
        for path in CATALOGUE_PATHS:
            url = self.cdn_host + path
            for attempt in range(retries + 1):
                try:
                    resp = self.session.get(url, timeout=self.timeout)
                except Exception as exc:
                    last_err = repr(exc)
                    break
                _check_quota(resp)
                if _is_login_page(resp):
                    if attempt < retries:
                        self._logged_in = False
                        self.login()
                        continue
                    last_err = f"{path} returned the login page"
                    break
                if resp.status_code >= 400:
                    last_err = f"{path} -> HTTP {resp.status_code}"
                    break
                try:
                    cams = _normalise(resp.json())
                except ValueError as exc:
                    last_err = f"{path} -> not JSON ({exc})"
                    break
                if cams:
                    log.info("catalogue: %d camera(s) from %s", len(cams), path)
                    return cams
                last_err = f"{path} -> empty"
                break
        raise RuntimeError(f"could not read catalogue: {last_err}")

    def quota_exhausted(self) -> bool:
        """Cheap HTTP check (costs no watch time). Call after a stream fails
        to open, before trying the next one — RTSP only says 401."""
        try:
            _check_quota(self.session.get(self.cdn_host + CATALOGUE_PATHS[0], timeout=self.timeout))
        except WatchQuotaExceeded:
            return True
        except Exception:
            pass
        return False

    # -- URL construction -------------------------------------------------
    def _userinfo(self) -> str:
        # '@' in the email must be %40; the password may contain specials too.
        return f"{quote(self.email, safe='')}:{quote(self._password, safe='')}"

    def rtsp_url(self, cam_id: str) -> str:
        return f"rtsp://{self._userinfo()}@{self.media_host}:{RTSP_PORT}/stream/{cam_id}"

    def whep_url(self, cam_id: str) -> str:
        return f"http://{self._userinfo()}@{self.media_host}:{WHEP_PORT}/stream/{cam_id}/whep"

    def hls_url(self, cam_id: str) -> str:
        # note: no /stream/ prefix on the HLS path
        return f"{self.cdn_host}/{cam_id}/index.m3u8"

    def stream_url(self, cam_id: str, transport: Transport | None = None) -> str:
        t = transport or self._transport or self.choose_transport()
        return self.rtsp_url(cam_id) if t == "rtsp" else self.hls_url(cam_id)

    def capture_options(self, transport: Transport) -> str:
        """Value for OPENCV_FFMPEG_CAPTURE_OPTIONS.

        RTSP: force TCP. HLS: hand FFmpeg the session cookie, since the CDN host
        is behind the access password.
        """
        if transport == "rtsp":
            return "rtsp_transport;tcp"
        cookie = self.cookie_header
        return f"headers;Cookie: {cookie}\r\n" if cookie else ""

    # -- transport --------------------------------------------------------
    def choose_transport(self) -> Transport:
        if self._transport is None:
            report = self.transport_report()
            self._transport = report["recommended_transport"]  # type: ignore[assignment]
        return self._transport  # type: ignore[return-value]

    def transport_report(self) -> dict[str, Any]:
        """RTSP and WHEP bypass the CDN and hit the public IP directly, so a
        corporate firewall that allows 443 may still block 8554. Establish this
        in five seconds instead of debugging a dead capture for an hour."""
        cdn = urlparse(self.cdn_host).hostname or ""
        ports = {
            f"cdn {cdn}:443": _tcp_open(cdn, 443),
            f"media {self.media_host}:{RTSP_PORT} (rtsp)": _tcp_open(self.media_host, RTSP_PORT),
            f"media {self.media_host}:{WHEP_PORT} (whep)": _tcp_open(self.media_host, WHEP_PORT),
        }
        rtsp_ok = ports[f"media {self.media_host}:{RTSP_PORT} (rtsp)"]
        out: dict[str, Any] = {
            "ports": ports,
            "recommended_transport": "rtsp" if rtsp_ok else "hls",
            "webrtc_udp_port": WEBRTC_UDP_PORT,
        }
        if not rtsp_ok:
            out["note"] = (
                f"{self.media_host}:{RTSP_PORT} unreachable — the CDN cannot proxy RTSP, so "
                "this is your network blocking outbound 8554, not the grid being down. "
                "Fall back to HLS over 443 (coarser PTS granularity, higher latency), or "
                "open 8554/TCP outbound. Keep all timing on PTS either way."
            )
        return out


def _tcp_open(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _normalise(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    if isinstance(payload, dict):
        for key in ("cameras", "items", "data", "results", "streams"):
            val = payload.get(key)
            if isinstance(val, list):
                return [c for c in val if isinstance(c, dict)]
        return [v for v in payload.values() if isinstance(v, dict)]
    raise ValueError(f"unexpected catalogue shape: {type(payload)}")


def camera_id_of(cam: dict[str, Any]) -> str:
    for key in ("id", "cam_id", "camera_id", "name", "path"):
        val = cam.get(key)
        if isinstance(val, str) and val:
            return val.rsplit("/", 1)[-1]
    raise ValueError(f"no camera id in {cam!r}")


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SentinelSession() as s:
        cams = s.catalogue()
        print(f"\n{len(cams)} camera(s)\n")
        print(json.dumps(cams[:3], indent=2)[:2500])
        print("\ntransport probe:")
        print(json.dumps(s.transport_report(), indent=2))
        if cams:
            cid = camera_id_of(cams[0])
            print("\nexample URLs (credentials redacted):")
            print("  rtsp:", redact(s.rtsp_url(cid)))
            print("  hls :", s.hls_url(cid))
            print("  whep:", redact(s.whep_url(cid)))
