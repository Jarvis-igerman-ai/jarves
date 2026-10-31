"""Run the external workflow console directly from a checkout (stdlib only)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarves.workflow.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
