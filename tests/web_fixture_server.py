"""Creates reproducible browser-test inputs and serves the real local API."""
from pathlib import Path
import sys

from PIL import Image
import uvicorn

from synthetic import make_scene

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firewatch.web.app import create_app


if __name__ == "__main__":
    target = ROOT / "out" / "browser-tests"
    target.mkdir(parents=True, exist_ok=True)
    swir, tc, _ = make_scene(smoke=True)
    Image.fromarray(swir).save(target / "swir.png")
    Image.fromarray(tc).save(target / "truecolor.png")
    clear, _, _ = make_scene(burned=False, edge_fire=False, isolated_hot=False)
    Image.fromarray(clear).save(target / "clear.png")
    uvicorn.run(create_app(target / "jobs"), host="127.0.0.1", port=8001, log_level="warning")
