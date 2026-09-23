import json
import subprocess
import sys

from conftest import ROOT


def test_exports_the_openapi_document() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "gate.openapi"],
        capture_output=True,
        check=True,
        cwd=ROOT,
        env={"PATH": ""},  # no SECRET_KEY or DEV: the export mustn't need them
        text=True,
    )

    spec = json.loads(result.stdout)
    assert spec["openapi"].startswith("3.")
    assert "/api/v1/orgs/{org_id}/requests" in spec["paths"]
