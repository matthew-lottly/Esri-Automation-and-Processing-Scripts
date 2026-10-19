"""Write-once, verified report storage on a (shared) drive: <root>/YYYY-MM/<file>."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


class ReportStoreError(RuntimeError):
    pass


class ReportStore:
    def __init__(self, root: str | Path, label: str, tz: str = "UTC"):
        self.root, self.label, self.tz = Path(root), label, ZoneInfo(tz)

    def month_dir(self, when: datetime) -> Path:
        return self.root / when.astimezone(self.tz).strftime("%Y-%m")     # folder month = LOCAL month

    def safe_name(self, leak_id: str, when: datetime, kind: str) -> str:
        return f"{re.sub(r'[^A-Za-z0-9_.-]', '_', leak_id)}_{when.astimezone(self.tz):%Y%m%dT%H%M%S}_{kind}.pdf"

    def write(self, data: bytes, leak_id: str, when: datetime, kind: str) -> Path:
        """Write atomically, refuse to overwrite, verify by read-back, store a SHA-256 sidecar, mark read-only."""
        if not data.startswith(b"%PDF"):
            raise ReportStoreError("refusing to store non-PDF data")
        folder = self.month_dir(when)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            final = folder / self.safe_name(leak_id, when, kind)
            if final.exists():
                raise ReportStoreError(f"{final} already exists; reports are write-once")
            tmp = final.with_suffix(".pdf.tmp")
            with open(tmp, "wb") as fh:
                fh.write(data); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, final)
            digest = hashlib.sha256(data).hexdigest()
            if hashlib.sha256(final.read_bytes()).hexdigest() != digest:
                raise ReportStoreError(f"read-back hash mismatch for {final}")
            final.with_suffix(".pdf.sha256").write_text(f"{digest}  {final.name}\n")
            try:
                os.chmod(final, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass   # some network shares reject chmod; enforce immutability with share permissions
            return final
        except OSError as exc:
            raise ReportStoreError(f"{self.label}: cannot write to {folder}: {exc}") from exc

    def list_pdfs(self, month: datetime) -> list[Path]:
        d = self.month_dir(month)
        return sorted(d.glob("*.pdf")) if d.exists() else []
