"""Print the API's OpenAPI document; the frontend generates its types from it.

    uv run python -m gate.openapi > frontend/openapi.json
"""

import json
import sys

from gate.config import Settings
from gate.main import create_app


def main() -> None:
    # Building the app needs settings, but nothing is served or signed here,
    # so the dev key is fine.
    app = create_app(Settings(dev=True))
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
