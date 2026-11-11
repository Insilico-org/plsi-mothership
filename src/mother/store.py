"""Mother storage (WIRE §6) behind one interface: read / write / append / list / delete on `/`-separated paths,
and write_sheet for the stewards' dashboard.
MOTHER_STORE selects the backend: `local:<dir>` (dev and tests) or `drive:<shared drive id>` (Insilico's Google
Drive shared drive; service account file from MOTHER_DRIVE_CREDENTIALS). Nothing else reads the storage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Protocol

from mother.errors import EnvironmentProblem, NotFound, UsageError

FOLDER = "application/vnd.google-apps.folder"
SHEET = "application/vnd.google-apps.spreadsheet"
WIDE = 60  # a column with longer text is set to WIDE_PX and wraps
WIDE_PX = 420
HEADER = "#e6f6e7"
Tabs = dict[str, list[list[Any]]]


class Store(Protocol):
    def read(self, path: str) -> str: ...
    def write(self, path: str, text: str) -> None: ...
    def append(self, path: str, line: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, path: str) -> bool: ...  # False: some of it only went to the backend's trash
    def exists(self, path: str) -> bool: ...
    def write_sheet(self, title: str, tabs: Tabs, colors: dict[str, str]) -> str: ...  # where the stewards open it


def _parts(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p]
    if any(p in (".", "..") or "\\" in p or ":" in p for p in parts):
        raise UsageError("bad storage path")
    return parts


class LocalStore:
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

    def delete(self, path: str) -> bool:
        p = self._path(path)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        return True

    def exists(self, path: str) -> bool:
        return self._path(path).exists()

    def write_sheet(self, title: str, tabs: Tabs, colors: dict[str, str]) -> str:
        """One CSV per tab under <title>/, old tabs removed (colors are for a real sheet only)."""
        self.delete(title)
        for tab, rows in tabs.items():
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(rows)
            self.write(f"{title}/{tab}.csv", buf.getvalue())
        return str(self._path(title))


def _rgb(hex_color: str) -> dict[str, float]:
    return {k: int(hex_color[i:i + 2], 16) / 255 for k, i in (("red", 1), ("green", 3), ("blue", 5))}


def _tab_format(sheet_id: int, rows: list[list[Any]]) -> list[dict[str, Any]]:
    """Bold shaded header row, frozen; columns sized to their text, long text wrapped at a reading width."""
    width = max((len(r) for r in rows), default=1)
    out: list[dict[str, Any]] = [
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": _rgb(HEADER)}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0,
                                                 "endIndex": width}}}]
    for col in range(width):
        if max((len(str(r[col])) for r in rows if col < len(r)), default=0) > WIDE:
            span = {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1}
            out += [{"updateDimensionProperties": {"range": span, "properties": {"pixelSize": WIDE_PX}, "fields": "pixelSize"}},
                    {"repeatCell": {"range": {"sheetId": sheet_id, "startColumnIndex": col, "endColumnIndex": col + 1},
                                    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                                    "fields": "userEnteredFormat.wrapStrategy"}}]
    return out


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
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
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

    def _folder(self, parts: list[str]) -> dict[str, str]:
        """The first folder at `parts`, made where missing."""
        item = {"id": self.drive, "mimeType": FOLDER}
        for name in parts:
            found = self._children(item["id"], name)
            item = found[0] if found else self.files.create(
                body={"name": name, "mimeType": FOLDER, "parents": [item["id"]]}, fields="id, mimeType",
                **self.common).execute()
        return item

    def _tabs(self, sid: str) -> dict[str, dict[str, Any]]:
        fields = "sheets(properties(sheetId,title),conditionalFormats)"
        return {t["properties"]["title"]: t for t in self.sheets.get(spreadsheetId=sid, fields=fields).execute()["sheets"]}

    def shared(self, file_id: str) -> dict[str, Any]:
        """Metadata of a file someone shared with the service account; nothing is downloaded."""
        fields = "id, name, mimeType, size, sha256Checksum, headRevisionId, modifiedTime"
        return self.files.get(fileId=file_id, fields=fields, **self.common).execute()

    def shared_bytes(self, file_id: str) -> bytes:
        """The file's bytes; never with acknowledgeAbuse, so a file flagged as malware is not fetched."""
        return self.files.get_media(fileId=file_id, **self.common).execute()

    def read(self, path: str) -> str:
        item = next((i for i in self._all(_parts(path)) if i["mimeType"] != FOLDER), None)
        if item is None:
            raise NotFound("not in the store")
        return self.shared_bytes(item["id"]).decode("utf-8")

    def write(self, path: str, text: str) -> None:
        from googleapiclient.http import MediaInMemoryUpload

        *folders, name = _parts(path)
        parent = self._folder(folders)
        media = MediaInMemoryUpload(text.encode("utf-8"), mimetype=mimetypes.guess_type(name)[0] or "text/plain")
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

    def delete(self, path: str) -> bool:
        """Delete for good; True when everything is gone. On a shared drive only a Manager may delete: a Content
        manager service account is refused (403), so the item goes to the drive's trash (emptied after 30 days) and
        the result is False, for the caller to tell whoever asked for the deletion."""
        gone = True
        for item in self._all(_parts(path)):
            try:
                self.files.delete(fileId=item["id"], **self.common).execute()
            except Exception as exc:  # noqa: BLE001 - googleapiclient's HttpError, imported only with the extra
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 404:  # already gone
                    continue
                if status != 403:
                    raise
                self.files.update(fileId=item["id"], body={"trashed": True}, **self.common).execute()
                gone = False
        return gone

    def exists(self, path: str) -> bool:
        return bool(self._all(_parts(path)))

    def write_sheet(self, title: str, tabs: Tabs, colors: dict[str, str]) -> str:
        """The Google Sheet `title` at the top of the drive, made once and rewritten tab by tab. Values go in RAW,
        so text starting with "=" stays text and never runs as a formula; `colors` shades cells by exact text."""
        found = [c for c in self._children(self.drive, title) if c["mimeType"] == SHEET]
        sid = found[0]["id"] if found else self.files.create(
            body={"name": title, "mimeType": SHEET, "parents": [self.drive]}, fields="id", **self.common).execute()["id"]
        have = self._tabs(sid)
        if adds := [{"addSheet": {"properties": {"title": t}}} for t in tabs if t not in have]:
            self.sheets.batchUpdate(spreadsheetId=sid, body={"requests": adds}).execute()
            have = self._tabs(sid)
        requests = [{"deleteSheet": {"sheetId": v["properties"]["sheetId"]}} for t, v in have.items() if t not in tabs]
        for tab, rows in tabs.items():
            sheet_id = have[tab]["properties"]["sheetId"]
            self.sheets.values().clear(spreadsheetId=sid, range=f"'{tab}'").execute()
            requests += _tab_format(sheet_id, rows)
            if not have[tab].get("conditionalFormats"):  # once: rebuilds never stack duplicate rules
                requests += [{"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": sheet_id}],
                    "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                                    "format": {"backgroundColor": _rgb(color)}}}}} for text, color in colors.items()]
        self.sheets.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": [
            {"range": f"'{tab}'!A1", "values": rows} for tab, rows in tabs.items()]}).execute()
        self.sheets.batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()
        return f"https://docs.google.com/spreadsheets/d/{sid}"


def open_store() -> Store:
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


def new_receipt() -> str:
    """A receipt `r_<16 hex>`: the name of everything one command stores."""
    return f"r_{secrets.token_hex(8)}"


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def read_json(store: Store, path: str, default: Any = None) -> Any:
    """The document at `path`; `default` when it is missing and a default is given, else NotFound."""
    try:
        return json.loads(store.read(path))
    except NotFound:
        if default is None:
            raise
        return default


def write_json(store: Store, path: str, doc: Any) -> None:
    store.write(path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def append_jsonl(store: Store, path: str, doc: dict[str, Any]) -> None:
    store.append(path, json.dumps(doc, ensure_ascii=False, sort_keys=True))


def read_jsonl(store: Store, path: str) -> list[dict[str, Any]]:
    try:
        text = store.read(path)
    except NotFound:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]
