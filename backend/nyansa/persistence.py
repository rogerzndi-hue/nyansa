"""Utilitaires de persistance locale s?curis?e."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, data: Any) -> None:
    """?crit un JSON puis remplace atomiquement le fichier cible."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(data, ensure_ascii=False)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        if os.name == "posix":
            os.chmod(temporary, 0o600)

        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
