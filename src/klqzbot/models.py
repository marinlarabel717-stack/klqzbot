from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CloneStats:
    scanned: int = 0
    eligible: int = 0
    invited: int = 0
    skipped: int = 0
    failed: int = 0
