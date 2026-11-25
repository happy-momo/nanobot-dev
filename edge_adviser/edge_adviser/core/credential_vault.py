"""Encrypted backend-only storage for connector credentials."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    """Persist connector credentials encrypted with a deployment-provided key."""

    def __init__(self, path: Path, key: str | None = None):
        self.path = path
        self._fernet = Fernet(key.encode("ascii")) if key else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def get(self, credential_id: str) -> dict[str, str]:
        if not credential_id:
            return {}
        records = self._read()
        try:
            return dict(records[credential_id])
        except KeyError:
            raise KeyError(f"connector credentials not found: {credential_id}") from None

    def put(self, credential_id: str, values: Mapping[str, str]) -> None:
        records = self._read()
        records[credential_id] = dict(values)
        self._write(records)

    def delete(self, credential_id: str) -> None:
        records = self._read()
        if credential_id not in records:
            return
        del records[credential_id]
        self._write(records)

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        fernet = self._require_fernet()
        try:
            raw = fernet.decrypt(self.path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("connector credential vault cannot be decrypted or is invalid") from exc
        if not isinstance(data, dict) or any(
            not isinstance(credential_id, str)
            or not isinstance(headers, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in headers.items())
            for credential_id, headers in data.items()
        ):
            raise ValueError("connector credential vault has an invalid shape")
        return data

    def _write(self, records: dict[str, dict[str, str]]) -> None:
        fernet = self._require_fernet()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = fernet.encrypt(json.dumps(records, ensure_ascii=False).encode("utf-8"))
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _require_fernet(self) -> Fernet:
        if self._fernet is None:
            raise RuntimeError(
                "connector credentials are locked; set EDGE_ADVISER_CREDENTIAL_KEY "
                "to a Fernet key before saving or using them")
        return self._fernet
