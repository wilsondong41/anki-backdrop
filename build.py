"""Package the add-on into dist/: backdrop_video.ankiaddon (to share) and backdrop_video-ankiweb.ankiaddon (no manifest)."""

import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "backdrop_video")
DIST = os.path.join(ROOT, "dist")
FILES = ["__init__.py", "live.py", "config.json", "manifest.json", "web/backdrop.js", "web/backdrop.css"]


def package(out: str, skip: set) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            if name not in skip:
                z.write(os.path.join(SRC, name), name)
    print(f"built {out}")


if __name__ == "__main__":
    os.makedirs(DIST, exist_ok=True)
    package(os.path.join(DIST, "backdrop_video.ankiaddon"), set())
    package(os.path.join(DIST, "backdrop_video-ankiweb.ankiaddon"), {"manifest.json"})
