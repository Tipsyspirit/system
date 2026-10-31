"""Start the app. Host and port come from rules.txt."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))
import uvicorn
from app.rules import load

if __name__ == "__main__":
    r = load()  # exits with a clear error if rules.txt is invalid
    uvicorn.run("app.main:app", host=r.s("backend", "host"), port=r.i("backend", "port"), app_dir="backend")
