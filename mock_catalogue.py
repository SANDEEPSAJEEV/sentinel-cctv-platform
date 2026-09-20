"""
Mock of the Sentinel sandbox catalogue endpoint.

The organiser's guide says: "Always start from the catalogue rather than
hard-coding endpoints ... the catalogue is the contract, the URL pattern is
not."  So every client in this project reads /api/ingest and nothing else.

This server reproduces that contract against the local MediaMTX replica by
asking MediaMTX's own control API which paths are currently publishing, then
describing them the way the real grid does.

    pip install fastapi uvicorn httpx
    python mock_catalogue.py
    curl -s http://localhost:8080/api/ingest | python -m json.tool

When you get real credentials, point your tooling at the Sentinel host instead.
No client code changes.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

MEDIAMTX_API = os.environ.get("MEDIAMTX_API", "http://localhost:9997")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "localhost")
PORT = int(os.environ.get("PORT", "8080"))

# Stand-ins for the five departments named in FAQ 39. Purely cosmetic locally,
# but it keeps the registry schema honest from day one.
DEPARTMENTS = ["Police", "Municipal Corporation", "GSRTC", "Panchayat", "Health"]
PLACES = [
    ("Gandhinagar Sector 18", 23.2156, 72.6369),
    ("Ahmedabad Kalupur", 23.0276, 72.6019),
    ("Surat Udhna", 21.1702, 72.8311),
    ("Vadodara Alkapuri", 22.3072, 73.1812),
    ("Rajkot Kalavad Road", 22.3039, 70.8022),
    ("Jamnagar Bedi Gate", 22.4707, 70.0577),
]

app = FastAPI(title="Sentinel mock catalogue")


def _codec_of(track: dict[str, Any]) -> str:
    for key in ("codec", "type"):
        val = track.get(key)
        if isinstance(val, str):
            return val.upper()
    return "UNKNOWN"


async def _mediamtx_paths() -> list[dict[str, Any]]:
    url = f"{MEDIAMTX_API}/v3/paths/list"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json().get("items", [])


@app.get("/api/ingest")
async def ingest() -> JSONResponse:
    try:
        items = await _mediamtx_paths()
    except Exception as exc:  # server not up yet — answer honestly, don't crash
        return JSONResponse(
            {"cameras": [], "error": f"mediamtx unreachable: {exc}"}, status_code=503
        )

    cameras: list[dict[str, Any]] = []
    for idx, item in enumerate(sorted(items, key=lambda i: str(i.get("name", "")))):
        name = str(item.get("name", ""))           # e.g. "stream/1"
        cam_id = name.rsplit("/", 1)[-1] or str(idx + 1)
        ready = bool(item.get("ready", False))
        tracks = item.get("tracks") or []
        codecs = [_codec_of(t) if isinstance(t, dict) else str(t).upper() for t in tracks]
        video_codec = next((c for c in codecs if "26" in c or "AV1" in c), "UNKNOWN")

        place, lat, lon = PLACES[idx % len(PLACES)]
        cameras.append(
            {
                "id": cam_id,
                "name": f"CAM-{cam_id.zfill(3)}",
                "department": DEPARTMENTS[idx % len(DEPARTMENTS)],
                "location": {"label": place, "lat": lat, "lon": lon},
                "codec": video_codec,
                "live": ready,
                "stream": {
                    "declared_fps": None,   # never trust this; measure it
                    "tracks": codecs,
                },
                "urls": {
                    "rtsp": f"rtsp://{PUBLIC_HOST}:8554/{name}",
                    "whep": f"http://{PUBLIC_HOST}:8889/{name}/whep",
                    "hls": f"http://{PUBLIC_HOST}:8888/{name}/index.m3u8",
                },
            }
        )

    return JSONResponse({"count": len(cameras), "cameras": cameras})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
