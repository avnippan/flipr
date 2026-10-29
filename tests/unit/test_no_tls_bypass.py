"""Guard: nothing outside the archived scrapers may turn off TLS verification."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = {"scrapers"}
PATTERN = re.compile(r"verify\s*=\s*False|disable_warnings")


def test_no_verify_false_in_active_code():
    offenders = []
    for base in ("backend/app", "tests"):
        for path in (ROOT / base).rglob("*.py"):
            if SKIP & set(path.parts) or path.name == Path(__file__).name:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PATTERN.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert not offenders, f"TLS verification disabled at: {offenders}"
