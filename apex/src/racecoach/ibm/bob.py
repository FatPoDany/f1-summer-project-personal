"""Read archived IBM Bob exports (docs/bob/exports/) into the feedback prompt.

The dated filename convention (YYYY-MM-DD-<topic>.md) makes lexical order
chronological, so "newest" needs no metadata. Templates are ignored by name.
Errors are readable: this path is driven from the CLI by people mid-demo.
"""

import os
from pathlib import Path

DEFAULT_EXPORTS = Path("docs/bob/exports")
PROMPT_CHAR_LIMIT = 6000  # keep the metrics, not the code tour, as the prompt's bulk


class BobExportError(ValueError):
    """A Bob export we refuse to feed the coach. Messages are for people."""


def exports_root() -> Path:
    return Path(os.environ.get("RACECOACH_BOB_EXPORTS", str(DEFAULT_EXPORTS)))


def list_exports() -> list[Path]:
    root = exports_root()
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.suffix in (".md", ".txt") and "template" not in path.stem.lower()
    )


def newest_export() -> Path:
    exports = list_exports()
    if not exports:
        raise BobExportError(
            f"No IBM Bob exports found in {exports_root()}. Analyse the controller "
            "code in the Bob IDE and archive the answer first — the flow is "
            "documented in docs/bob/README.md."
        )
    return exports[-1]


def load_export(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise BobExportError(f"No such Bob export: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise BobExportError(f"{path.name} is empty — archive Bob's actual answer in it.")
    if len(text) > PROMPT_CHAR_LIMIT:
        text = text[:PROMPT_CHAR_LIMIT] + "\n[… trimmed for the prompt …]"
    return text
