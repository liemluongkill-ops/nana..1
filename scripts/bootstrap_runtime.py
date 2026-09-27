from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "nana" / "data"


def copy_if_missing(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def main() -> int:
    created: list[Path] = []
    for source, destination in (
        (ROOT / ".env.example", ROOT / ".env"),
        (DATA_DIR / "identity.example.json", DATA_DIR / "identity.json"),
        (DATA_DIR / "users.example.json", DATA_DIR / "users.json"),
    ):
        if copy_if_missing(source, destination):
            created.append(destination)

    for directory in (
        DATA_DIR / "external_bridge" / "requests",
        DATA_DIR / "external_bridge" / "replies",
        DATA_DIR / "external_bridge" / "outbox",
        DATA_DIR / "voice_cache",
        ROOT / "nana" / "runtime_logs",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    if created:
        print("Created local files:")
        for item in created:
            print(f"  {item.relative_to(ROOT)}")
    else:
        print("Local configuration already exists; nothing overwritten.")
    print("Edit .env locally before starting Nana.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
