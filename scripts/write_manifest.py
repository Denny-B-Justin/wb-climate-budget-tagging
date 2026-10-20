"""Write manifest.json for Posit Connect (python-dash). Re-run after every change you deploy:

    python scripts/write_manifest.py

(`rsconnect write-manifest dash` does the same job if you prefer the official tool.)
Secrets (.env), tests, local data and caches are never listed.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", "tests", "scripts", "data", "reports", ".git", ".venv", ".pytest_cache", "node_modules", "sql"}
SKIP_FILES = {".env", "manifest.json", "requirements-dev.txt"}


def main() -> None:
    files = {}
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or set(rel.parts[:-1]) & SKIP_DIRS or rel.name in SKIP_FILES or rel.suffix in (".pyc", ".docx"):
            continue
        files[rel.as_posix()] = {"checksum": hashlib.md5(p.read_bytes()).hexdigest()}
    manifest = {
        "version": 1,
        "locale": "English_United States.1252",
        "metadata": {"appmode": "python-dash", "entrypoint": "app.py"},
        "python": {"version": "3.9.13", "package_manager": {"name": "pip", "version": "22.0.4", "package_file": "requirements.txt"}},
        "files": files,
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("manifest.json written with %d files" % len(files))


if __name__ == "__main__":
    main()
