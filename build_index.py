"""Genera catalog.js amb l'índex complet dels arxius locals d'Atenea i l'estat de sync.log.

Ús:
    python build_index.py
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "Assignatures"
LOG_FILE = ROOT / "sync.log"
OUT_FILE = ROOT / "catalog.js"
CONFIG_FILE = ROOT / "config.json"
SUBJECTS_FILE = ROOT / "subjects.json"

VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}
OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
CODE_EXTS = {
    ".py", ".ipynb", ".dat", ".txt", ".msh", ".stp",
    ".mltbx", ".sscp", ".md", ".csv", ".json",
}
TEXT_EXTS = {".txt", ".py", ".dat", ".md", ".csv", ".json"}
ARCHIVE_EXTS = {".7z", ".rar", ".zip", ".tar", ".gz"}

MAX_HTML_BYTES = 200 * 1024
MAX_TEXT_BYTES = 64 * 1024
DEFAULT_LARGE_MB = 50
RECENT_SECONDS = 7 * 86400

PALETTE: list[tuple[str, str]] = [
    ("#D9480F", "#FF8A4C"),
    ("#7048E8", "#A88BFF"),
    ("#0C8599", "#3BC4D6"),
    ("#B58800", "#E6C04A"),
    ("#1C7ED6", "#5AA9F0"),
    ("#2B8A3E", "#5CC46F"),
    ("#C2255C", "#F06595"),
    ("#475569", "#94A3B8"),
]

LOG_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.*)$")
DONE_LINE_RE = re.compile(
    r"^Hecho:\s*(\d+)\s+nuevos,\s*(\d+)\s+actualizados,.*?(\d+)\s+errores",
    re.IGNORECASE,
)


def natural_key(text: str) -> list[tuple[int, int | str]]:
    """Clau d'ordenació natural: números per valor i text sense distinció de majúscules."""
    parts = re.split(r"(\d+)", (text or "").lower())
    out: list[tuple[int, int | str]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            out.append((0, int(part)))
        else:
            out.append((1, part))
    return out


def get_max_repo_bytes() -> int:
    """Llegeix max_repo_file_mb de config.json (mai el token)."""
    if not CONFIG_FILE.exists():
        return DEFAULT_LARGE_MB * 1024 * 1024
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        mb = float(cfg.get("max_repo_file_mb", DEFAULT_LARGE_MB))
        return int(mb * 1024 * 1024)
    except Exception:
        return DEFAULT_LARGE_MB * 1024 * 1024


def load_subject_overrides() -> dict[str, tuple[str | None, str | None, str | None]]:
    """Llegeix personalitzacions opcionals de nom i colors des de subjects.json."""
    if not SUBJECTS_FILE.exists():
        return {}
    try:
        raw = json.loads(SUBJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, tuple[str | None, str | None, str | None]] = {}
    for key, val in raw.items():
        k = str(key).strip()
        if not k:
            continue
        if isinstance(val, (list, tuple)) and len(val) >= 1:
            name = str(val[0]).strip() if val[0] else None
            c_light = str(val[1]).strip() if len(val) > 1 and val[1] else None
            c_dark = str(val[2]).strip() if len(val) > 2 and val[2] else None
            overrides[k] = (name, c_light, c_dark)
        elif isinstance(val, dict):
            name = str(val["name"]).strip() if val.get("name") else None
            c_light = str(val["colorLight"]).strip() if val.get("colorLight") else None
            c_dark = str(val["colorDark"]).strip() if val.get("colorDark") else None
            overrides[k] = (name, c_light, c_dark)
    return overrides


def resolve_subject_meta(
    folder_name: str,
    overrides: dict[str, tuple[str | None, str | None, str | None]] | None = None,
) -> tuple[str, str, str, str, str]:
    """Retorna (id, code, display_name, color_light, color_dark) per a una carpeta d'assignatura."""
    m = re.match(r"^(\d+)\s*-\s*(.+)$", folder_name.strip())
    if m:
        code = m.group(1)
        raw_name = m.group(2).strip()
        subj_id = code
    else:
        code = ""
        raw_name = folder_name.strip()
        subj_id = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-") or "assignatura"

    name = raw_name.title() if raw_name.isupper() else raw_name
    idx = zlib.crc32(folder_name.encode("utf-8")) % len(PALETTE)
    c_light, c_dark = PALETTE[idx]

    if overrides:
        ov = overrides.get(code) or overrides.get(folder_name) or overrides.get(subj_id)
        if ov:
            if ov[0]:
                name = ov[0]
            if ov[1]:
                c_light = ov[1]
            if ov[2]:
                c_dark = ov[2]

    return subj_id, code, name, c_light, c_dark


def classify_kind(ext: str) -> str:
    """Classifica el tipus de fitxer per als filtres i el visor de la UI."""
    if ext == ".pdf":
        return "pdf"
    if ext == ".url":
        return "url"
    if ext in {".html", ".htm"}:
        return "html"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in CODE_EXTS:
        return "code"
    if ext in ARCHIVE_EXTS:
        return "archive"
    return "other"


def extract_url_target(path: Path) -> tuple[str, bool]:
    """Extreu l'enllaç real d'un arxiu .url de Windows."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", False
    target = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:4].upper() == "URL=":
            target = stripped[4:].strip()
            break
    if not target:
        return "", False
    host = urllib.parse.urlparse(target).netloc.lower()
    is_yt = host == "youtu.be" or host.endswith(".youtu.be") or host == "youtube.com" or host.endswith(".youtube.com")
    return target, is_yt


def parse_sync_log() -> dict:
    """Analitza sync.log per obtenir l'últim estat de sincronització, historial recent i esdeveniments per arxiu."""
    if not LOG_FILE.exists():
        return {
            "lastSync": None,
            "lastRunWithChanges": None,
            "recentRuns": [],
            "fileEvents": {},
        }

    runs: list[dict] = []
    current_run: dict | None = None
    file_events: dict[str, dict[str, str]] = {}

    def new_run(ts: str) -> dict:
        return {
            "startedAt": ts,
            "finishedAt": None,
            "ok": True,
            "new": 0,
            "updated": 0,
            "errors": 0,
            "errorMessage": None,
            "files": [],
        }

    def close_current() -> None:
        nonlocal current_run
        if current_run is not None:
            if current_run["finishedAt"] is None:
                current_run["finishedAt"] = current_run["startedAt"]
            runs.append(current_run)
            current_run = None

    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    for raw_line in lines:
        m = LOG_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        ts, msg = m.group(1), m.group(2).strip()

        if msg.startswith("Conectado como "):
            close_current()
            current_run = new_run(ts)
            continue

        if msg.startswith("ERROR: no se puede conectar"):
            close_current()
            failed = new_run(ts)
            failed["finishedAt"] = ts
            failed["ok"] = False
            failed["errors"] = 1
            failed["errorMessage"] = msg
            runs.append(failed)
            continue

        if msg.startswith("ERROR: git push ha fallado"):
            target_run = current_run if current_run is not None else (runs[-1] if runs else None)
            if target_run is not None:
                target_run["ok"] = False
                target_run["errors"] = max(1, target_run["errors"])
                target_run["errorMessage"] = (
                    f"{target_run['errorMessage']}; {msg}" if target_run["errorMessage"] else msg
                )
            continue

        if current_run is None:
            current_run = new_run(ts)

        if msg.startswith("Nuevo: "):
            rel_norm = msg[len("Nuevo: "):].replace("\\", "/").strip()
            file_events[rel_norm.lower()] = {"action": "new", "at": ts}
            current_run["files"].append({"rel": rel_norm, "action": "new", "at": ts})
            continue

        if msg.startswith("Actualizando: "):
            rel_norm = msg[len("Actualizando: "):].replace("\\", "/").strip()
            file_events[rel_norm.lower()] = {"action": "updated", "at": ts}
            current_run["files"].append({"rel": rel_norm, "action": "updated", "at": ts})
            continue

        if msg.startswith("ERROR descargando ") or msg.startswith("ERROR leyendo "):
            current_run["ok"] = False
            if not current_run["errorMessage"]:
                current_run["errorMessage"] = msg
            continue

        done_m = DONE_LINE_RE.match(msg)
        if done_m:
            new_c = int(done_m.group(1))
            upd_c = int(done_m.group(2))
            err_c = int(done_m.group(3))
            current_run["new"] = new_c
            current_run["updated"] = upd_c
            current_run["errors"] = err_c
            current_run["finishedAt"] = ts
            current_run["ok"] = (err_c == 0 and current_run["errorMessage"] is None)
            runs.append(current_run)
            current_run = None

    close_current()

    last_sync = None
    if runs:
        last = runs[-1]
        last_sync = {
            "startedAt": last["startedAt"],
            "finishedAt": last["finishedAt"],
            "ok": last["ok"],
            "new": last["new"],
            "updated": last["updated"],
            "errors": last["errors"],
            "errorMessage": last["errorMessage"],
        }

    last_with_changes = None
    for r in reversed(runs):
        if r["new"] + r["updated"] > 0:
            last_with_changes = {
                "at": r["finishedAt"] or r["startedAt"],
                "new": r["new"],
                "updated": r["updated"],
            }
            break

    recent_runs = [
        {
            "startedAt": r["startedAt"],
            "finishedAt": r["finishedAt"],
            "ok": r["ok"],
            "new": r["new"],
            "updated": r["updated"],
            "errors": r["errors"],
            "errorMessage": r["errorMessage"],
            "files": r["files"][:50],
        }
        for r in runs
        if (r["new"] + r["updated"] > 0) or (not r["ok"])
    ][-15:]
    recent_runs.reverse()

    return {
        "lastSync": last_sync,
        "lastRunWithChanges": last_with_changes,
        "recentRuns": recent_runs,
        "fileEvents": file_events,
    }


def _parse_ts(ts_str: str | None) -> float:
    if not ts_str:
        return 0.0
    try:
        return time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def build_catalog() -> dict:
    """Escaneja Assignatures/ i construeix l'estructura completa del catàleg."""
    now_ts = time.time()
    max_bytes = get_max_repo_bytes()
    subject_overrides = load_subject_overrides()
    sync_info = parse_sync_log()
    file_events: dict[str, dict[str, str]] = sync_info["fileEvents"]

    subjects: list[dict] = []
    files: list[dict] = []
    html_contents: dict[str, str] = {}
    text_contents: dict[str, str] = {}
    by_kind: dict[str, int] = {}
    total_bytes = 0

    if DOCS_DIR.exists():
        subject_dirs = sorted(
            [d for d in DOCS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda p: natural_key(p.name),
        )
    else:
        subject_dirs = []

    for subj_dir in subject_dirs:
        subj_id, code, name, c_light, c_dark = resolve_subject_meta(subj_dir.name, subject_overrides)
        subj_files: list[tuple[tuple, dict]] = []
        subj_sections_set: set[str] = set()
        subj_bytes = 0
        subj_recent = 0

        for dirpath, dirnames, filenames in os.walk(subj_dir):
            dirnames[:] = sorted([d for d in dirnames if not d.startswith(".")], key=natural_key)
            current_dir = Path(dirpath)
            for fname in sorted(filenames, key=natural_key):
                if fname.startswith(".") or fname.endswith(".part"):
                    continue
                full_path = current_dir / fname
                try:
                    st = full_path.stat()
                except OSError:
                    continue

                rel_path = full_path.relative_to(DOCS_DIR)
                rel_parts = rel_path.parts
                rel_posix = "/".join(rel_parts)
                # rel_parts[0] és la carpeta de l'assignatura
                if len(rel_parts) >= 3:
                    section = rel_parts[1]
                    subpath = list(rel_parts[2:-1])
                else:
                    section = ""
                    subpath = []

                subj_sections_set.add(section)
                ext = full_path.suffix.lower()
                ext_clean = ext[1:] if ext.startswith(".") else ""
                kind = classify_kind(ext)
                size = int(st.st_size)
                mtime = int(st.st_mtime)
                local_only = (ext in VIDEO_EXTS) or (size > max_bytes)
                href = "Assignatures/" + "/".join(urllib.parse.quote(p, safe="") for p in rel_parts)

                record: dict = {
                    "id": 0,
                    "subjectId": subj_id,
                    "section": section,
                    "subpath": subpath,
                    "name": fname,
                    "ext": ext_clean,
                    "kind": kind,
                    "rel": rel_posix,
                    "href": href,
                    "size": size,
                    "mtime": mtime,
                    "localOnly": local_only,
                }

                if ext == ".url":
                    target_url, is_yt = extract_url_target(full_path)
                    if target_url:
                        record["targetUrl"] = target_url
                    if is_yt:
                        record["isYoutube"] = True
                elif ext in {".html", ".htm"}:
                    try:
                        raw_bytes = full_path.read_bytes()[:MAX_HTML_BYTES]
                        html_contents[rel_posix] = raw_bytes.decode("utf-8", errors="replace")
                    except OSError:
                        pass
                elif ext in TEXT_EXTS and size <= MAX_TEXT_BYTES:
                    try:
                        text_contents[rel_posix] = full_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass

                ev = file_events.get(rel_posix.lower())
                if ev:
                    record["syncedAt"] = ev["at"]
                    record["syncAction"] = ev["action"]

                ref_ts = _parse_ts(ev["at"]) if ev else float(mtime)
                if now_ts - ref_ts <= RECENT_SECONDS:
                    subj_recent += 1

                subj_bytes += size
                total_bytes += size
                by_kind[kind] = by_kind.get(kind, 0) + 1

                sort_key = (
                    natural_key(section),
                    tuple(natural_key(p) for p in subpath),
                    natural_key(fname),
                )
                subj_files.append((sort_key, record))

        subj_files.sort(key=lambda pair: pair[0])
        for _, rec in subj_files:
            rec["id"] = len(files)
            files.append(rec)

        sections_sorted = sorted(subj_sections_set, key=natural_key)
        subjects.append(
            {
                "id": subj_id,
                "folder": subj_dir.name,
                "code": code,
                "name": name,
                "colorLight": c_light,
                "colorDark": c_dark,
                "fileCount": len(subj_files),
                "totalBytes": subj_bytes,
                "recentCount": subj_recent,
                "sections": sections_sorted,
            }
        )

    return {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rootPath": str(DOCS_DIR),
        "stats": {
            "subjects": len(subjects),
            "files": len(files),
            "totalBytes": total_bytes,
            "byKind": by_kind,
        },
        "sync": {
            "lastSync": sync_info["lastSync"],
            "lastRunWithChanges": sync_info["lastRunWithChanges"],
            "recentRuns": sync_info["recentRuns"],
        },
        "subjects": subjects,
        "files": files,
        "htmlContents": html_contents,
        "textContents": text_contents,
    }


def write_catalog(data: dict | None = None) -> Path:
    """Escriu catalog.js de forma atòmica."""
    if data is None:
        data = build_catalog()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    tmp_path = OUT_FILE.with_name(OUT_FILE.name + ".tmp")
    tmp_path.write_text(f"window.ATENEA_DATA = {payload};\n", encoding="utf-8")
    os.replace(tmp_path, OUT_FILE)
    return OUT_FILE


def main() -> None:
    data = build_catalog()
    out = write_catalog(data)
    stats = data["stats"]
    print(
        f"[build_index] {out.name}: {stats['subjects']} assignatures, "
        f"{stats['files']} arxius ({stats['totalBytes'] / (1024 * 1024):.1f} MB)"
    )


if __name__ == "__main__":
    main()
