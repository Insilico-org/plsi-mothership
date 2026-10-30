"""Mother storage (WIRE §6) behind one interface: read / write / append / list / delete on `/`-separated paths.
MOTHER_STORE selects the backend: `local:<dir>` (dev and tests) or `drive:<shared drive id>` (Insilico's Google
Drive shared drive; service account file from MOTHER_DRIVE_CREDENTIALS). Nothing else reads the storage."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Protocol

from mother.errors import EnvironmentProblem, NotFound, UsageError

FOLDER = "application/vnd.google-apps.folder"


class Store(Protocol):
    def read(self, path: str) -> str: ...
    def write(self, path: str, text: str) -> None: ...
    def append(self, path: str, line: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, path: str) -> None: ...
    def exists(self, path: str) -> bool: ...


def _parts(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p]
    if any(p in (".", "..") or "\\" in p or ":" in p for p in parts):
        raise UsageError("bad storage path")
    return parts


class LocalStore:
    """A directory on disk."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, path: str) -> Path:
        return self.root.joinpath(*_parts(path))

    def read(self, path: str) -> str:
        p = self._path(path)
        if not p.is_file():
            raise NotFound("not in the store")
        return p.read_text(encoding="utf-8")

    def write(self, path: str, text: str) -> None:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, p)

    def append(self, path: str, line: str) -> None:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line.rstrip("\n") + "\n")

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def delete(self, path: str) -> None:
        p = self._path(path)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    def exists(self, path: str) -> bool:
        return self._path(path).exists()


class DriveStore:
    """A Google shared drive: path segments are folders, the last one a file. Appends rewrite the file (Drive has no
    append); the Mother runs one command at a time. Drive allows two items of one name in a folder: a read takes the first
    file found through every same-name folder, a write the first, list and delete take them all."""

    def __init__(self, drive_id: str, credentials: str) -> None:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            raise EnvironmentProblem("the Drive backend needs google-api-python-client", code="DEPENDENCY_MISSING",
                                     hint="pip install -e <repo>[drive]") from None
        creds = service_account.Credentials.from_service_account_file(
            credentials, scopes=["https://www.googleapis.com/auth/drive"])
        self.files = build("drive", "v3", credentials=creds, cache_discovery=False).files()
        self.drive = drive_id
        self.common = {"supportsAllDrives": True}

    def _children(self, parent: str, name: str | None = None) -> list[dict[str, str]]:
        q = f"'{parent}' in parents and trashed = false"
        if name is not None:
            q += " and name = '" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"
        out, token = [], None
        while True:
            page = self.files.list(q=q, corpora="drive", driveId=self.drive, includeItemsFromAllDrives=True,
                                   fields="nextPageToken, files(id, name, mimeType)", pageToken=token,
                                   **self.common).execute()
            out += page.get("files", [])
            if not (token := page.get("nextPageToken")):
                return out

    def _all(self, parts: list[str]) -> list[dict[str, str]]:
        """Every item at `parts`, through every same-name folder on the way."""
        items = [{"id": self.drive, "mimeType": FOLDER}]  # a shared drive's root folder id is the drive id
        for name in parts:
            items = [c for i in items if i["mimeType"] == FOLDER for c in self._children(i["id"], name)]
        return items

    def _find(self, parts: list[str], create: bool = False) -> dict[str, str] | None:
        item = {"id": self.drive, "mimeType": FOLDER}
        for name in parts:
            found = self._children(item["id"], name)
            if found:
                item = found[0]
            elif create:
                item = self.files.create(body={"name": name, "mimeType": FOLDER, "parents": [item["id"]]},
                                         fields="id, mimeType", **self.common).execute()
            else:
                return None
        return item

    def read(self, path: str) -> str:
        item = next((i for i in self._all(_parts(path)) if i["mimeType"] != FOLDER), None)
        if item is None:
            raise NotFound("not in the store")
        return self.files.get_media(fileId=item["id"], **self.common).execute().decode("utf-8")

    def write(self, path: str, text: str) -> None:
        from googleapiclient.http import MediaInMemoryUpload

        *folders, name = _parts(path)
        parent = self._find(folders, create=True)
        media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="application/json")
        if existing := self._children(parent["id"], name):
            self.files.update(fileId=existing[0]["id"], media_body=media, **self.common).execute()
        else:
            self.files.create(body={"name": name, "parents": [parent["id"]]}, media_body=media, **self.common).execute()

    def append(self, path: str, line: str) -> None:
        old = self.read(path) if self.exists(path) else ""
        self.write(path, old + line.rstrip("\n") + "\n")

    def list(self, prefix: str) -> list[str]:
        parts = _parts(prefix)
        out, stack = [], [(i["id"], "/".join(parts)) for i in self._all(parts) if i["mimeType"] == FOLDER]
        while stack:
            folder, at = stack.pop()
            for child in self._children(folder):
                path = f"{at}/{child['name']}" if at else child["name"]
                if child["mimeType"] == FOLDER:
                    stack.append((child["id"], path))
                else:
                    out.append(path)
        return sorted(set(out))

    def delete(self, path: str) -> None:
        for item in self._all(_parts(path)):
            self.files.delete(fileId=item["id"], **self.common).execute()

    def exists(self, path: str) -> bool:
        return bool(self._all(_parts(path)))


def open_store() -> Store:
    """The store named by MOTHER_STORE."""
    backend, _, where = os.environ.get("MOTHER_STORE", "").partition(":")
    if backend == "local" and where:
        return LocalStore(where)
    if backend == "drive" and where:
        credentials = os.environ.get("MOTHER_DRIVE_CREDENTIALS")
        if not credentials:
            raise EnvironmentProblem("MOTHER_DRIVE_CREDENTIALS is not set", code="KEY_MISSING",
                                     hint="the path of the service account JSON file, from the Grok secret store")
        return DriveStore(where, credentials)
    raise EnvironmentProblem("MOTHER_STORE is not set to local:<dir> or drive:<shared drive id>",
                             code="STORE_NOT_CONFIGURED")


def read_json(store: Store, path: str, default: Any = None) -> Any:
    if default is not None and not store.exists(path):
        return default
    return json.loads(store.read(path))


def write_json(store: Store, path: str, doc: Any) -> None:
    store.write(path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def append_jsonl(store: Store, path: str, doc: dict[str, Any]) -> None:
    store.append(path, json.dumps(doc, ensure_ascii=False, sort_keys=True))


def read_jsonl(store: Store, path: str) -> list[dict[str, Any]]:
    if not store.exists(path):
        return []
    return [json.loads(line) for line in store.read(path).splitlines() if line.strip()]
