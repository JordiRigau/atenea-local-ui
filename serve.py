"""Servidor HTTP local opcional per a Atenea (només 127.0.0.1).

Permet navegar pels arxius d'Assignatures/, veure vídeos amb Range requests,
obrir arxius amb l'aplicació nativa de Windows, mostrar-los a l'Explorador
i llançar la sincronització diària des de la UI.

Ús:
    python serve.py
    python serve.py --port 8765 --no-browser
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import build_index

ROOT = Path(__file__).resolve().parent
ROOT_RESOLVED = ROOT.resolve()
DOCS_DIR = (ROOT / "Assignatures").resolve()
BLOCKED_PERSONAL_NAMES = {"config.json", "sync.log", "ui_state.json", "personal_sources.json"}
INDEX_FILE = ROOT / "index.html"
ICON_FILE = ROOT / "atenea.ico"
CATALOG_FILE = ROOT / "catalog.js"
SYNC_SCRIPT = ROOT / "run_sync.ps1"
LOG_FILE = ROOT / "sync.log"
UI_STATE_FILE = ROOT / "ui_state.json"

DEFAULT_PORT = 8765
CHUNK_SIZE = 256 * 1024

EXTRA_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".py": "text/plain; charset=utf-8",
    ".dat": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ipynb": "application/json; charset=utf-8",
}

INLINE_EXTS = {
    ".pdf", ".mp4", ".m4v", ".mov", ".webm", ".ogg",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".html", ".htm", ".txt", ".py", ".dat", ".md", ".csv",
}

catalog_lock = threading.Lock()
ui_state_lock = threading.Lock()
sync_lock = threading.Lock()
sync_state: dict[str, object] = {
    "running": False,
    "startedAt": None,
    "finishedAt": None,
    "exitCode": None,
    "summary": None,
}


def resolve_doc_path(rel_path: str, *, unquote: bool = False) -> Path | None:
    """Resol una ruta relativa dins d'Assignatures/ rebutjant qualsevol traversal."""
    if not rel_path:
        return None
    raw = urllib.parse.unquote(rel_path) if unquote else rel_path
    if not raw or raw.startswith(("/", "\\")) or "\x00" in raw or "\\" in raw or ":" in raw:
        return None
    parts = raw.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return None
    candidate = (DOCS_DIR.joinpath(*parts)).resolve()
    if not candidate.is_relative_to(DOCS_DIR):
        return None
    if not candidate.is_file():
        return None
    return candidate


def resolve_personal_path(source_id: str, rel_path: str, *, unquote: bool = False) -> Path | None:
    """Resol una ruta dins d'una carpeta personal configurada rebutjant traversal i secrets."""
    if not source_id or not rel_path:
        return None
    personal = build_index.load_personal_sources()
    if source_id == "general":
        entry = personal.get("general")
    else:
        entry = personal.get("subjects", {}).get(source_id)
    if not entry:
        return None

    raw = urllib.parse.unquote(rel_path) if unquote else rel_path
    if not raw or raw.startswith(("/", "\\")) or "\x00" in raw or "\\" in raw or ":" in raw:
        return None
    parts = raw.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return None

    base_resolved = entry["dir"].resolve()
    candidate = base_resolved.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base_resolved) or not candidate.is_file():
        return None
    if candidate.is_relative_to(ROOT_RESOLVED) or candidate.name.lower() in BLOCKED_PERSONAL_NAMES:
        return None
    return candidate


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in EXTRA_MIME_TYPES:
        return EXTRA_MIME_TYPES[ext]
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def read_last_sync_summary() -> str | None:
    if not LOG_FILE.exists():
        return None
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        s = line.strip()
        if any(k in s for k in ("Hecho:", "ERROR:", "Conectado")):
            return s
    return lines[-1].strip() if lines else None


def load_ui_state() -> dict[str, dict[str, bool]]:
    """Llegeix l'estat dels desplegables des de ui_state.json."""
    with ui_state_lock:
        if not UI_STATE_FILE.exists():
            return {"folderStates": {}}
        try:
            raw = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"folderStates": {}}
    fs = raw.get("folderStates") if isinstance(raw, dict) else None
    if not isinstance(fs, dict):
        return {"folderStates": {}}
    clean = {
        str(k): bool(v)
        for k, v in fs.items()
        if isinstance(k, str) and len(k) <= 512
    }
    return {"folderStates": clean}


def save_ui_state(folder_states: dict) -> dict[str, dict[str, bool]]:
    """Desa l'estat dels desplegables de forma atòmica a ui_state.json."""
    if not isinstance(folder_states, dict) or len(folder_states) > 5000:
        raise ValueError("folderStates invàlid")
    clean = {
        str(k): bool(v)
        for k, v in folder_states.items()
        if isinstance(k, str) and len(k) <= 512
    }
    payload = {"folderStates": clean}
    with ui_state_lock:
        tmp = UI_STATE_FILE.with_name(UI_STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, UI_STATE_FILE)
    return payload


def run_sync_worker() -> None:
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SYNC_SCRIPT),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        with catalog_lock:
            build_index.write_catalog()
        summary = read_last_sync_summary()
        with sync_lock:
            sync_state["running"] = False
            sync_state["finishedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sync_state["exitCode"] = proc.returncode
            sync_state["summary"] = summary
    except Exception as exc:
        with sync_lock:
            sync_state["running"] = False
            sync_state["finishedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            sync_state["exitCode"] = 1
            sync_state["summary"] = f"Error executant sync: {exc}"


class AteneaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Silencia access logs verbosos llevat d'errors
        if sys.stderr is not None and args and len(args) >= 2 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)

    def _send_json(self, status: int, obj: object) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _is_valid_post_request(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not (
            host == "127.0.0.1"
            or host == "localhost"
            or host.startswith("127.0.0.1:")
            or host.startswith("localhost:")
        ):
            return False
        if self.headers.get("X-Atenea-UI") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            if parsed.hostname not in ("127.0.0.1", "localhost"):
                return False
        return True

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return None
        if length <= 0 or length > 512 * 1024:
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _serve_doc_file(self, path: Path) -> None:
        size = path.stat().st_size
        mime = guess_mime(path)
        ext = path.suffix.lower()
        range_hdr = self.headers.get("Range")

        start = 0
        end = size - 1
        partial = False

        if range_hdr and size > 0:
            m = re.match(r"^bytes=(\d*)-(\d*)$", range_hdr.strip())
            if not m or (not m.group(1) and not m.group(2)):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                suffix = int(m.group(2))
                start = max(0, size - suffix)
                end = size - 1

            if start >= size or end < start:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True

        length = (end - start + 1) if size > 0 else 0
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if ext in INLINE_EXTS:
            self.send_header("Content-Disposition", "inline")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        if length == 0:
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path

        if req_path in ("/", "/index.html"):
            if not INDEX_FILE.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "index.html no trobat"})
                return
            self._send_bytes(HTTPStatus.OK, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")
            return

        if req_path in ("/favicon.ico", "/atenea.ico"):
            if not ICON_FILE.exists():
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            icon_bytes = ICON_FILE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(len(icon_bytes)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(icon_bytes)
            return

        if req_path == "/catalog.js":
            with catalog_lock:
                build_index.write_catalog()
            self._send_bytes(HTTPStatus.OK, CATALOG_FILE.read_bytes(), "application/javascript; charset=utf-8")
            return

        if req_path == "/api/ping":
            with sync_lock:
                running = bool(sync_state["running"])
            self._send_json(HTTPStatus.OK, {"ok": True, "app": "atenea-local", "syncing": running})
            return

        if req_path == "/api/catalog":
            with catalog_lock:
                data = build_index.build_catalog()
                build_index.write_catalog(data)
            self._send_json(HTTPStatus.OK, data)
            return

        if req_path == "/api/sync-status":
            with sync_lock:
                snapshot = dict(sync_state)
            self._send_json(HTTPStatus.OK, snapshot)
            return

        if req_path == "/api/ui-state":
            self._send_json(HTTPStatus.OK, load_ui_state())
            return

        if req_path.startswith("/Assignatures/"):
            rel_encoded = req_path[len("/Assignatures/"):]
            target = resolve_doc_path(rel_encoded, unquote=True)
            if target is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Arxiu no trobat"})
                return
            self._serve_doc_file(target)
            return

        if req_path.startswith("/personal/"):
            rest = req_path[len("/personal/"):]
            if "/" not in rest:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Arxiu no trobat"})
                return
            source_id_enc, rel_enc = rest.split("/", 1)
            source_id = urllib.parse.unquote(source_id_enc)
            target = resolve_personal_path(source_id, rel_enc, unquote=True)
            if target is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Arxiu no trobat"})
                return
            self._serve_doc_file(target)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "No trobat"})

    def do_POST(self) -> None:
        if not self._is_valid_post_request():
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Petició no autoritzada"})
            return

        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path

        if req_path in ("/api/open", "/api/reveal"):
            body = self._read_json_body()
            rel = body.get("path") if body else None
            if not isinstance(rel, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Falta el camp path"})
                return
            if body.get("origin") == "personal" and isinstance(body.get("sourceId"), str):
                target = resolve_personal_path(body["sourceId"], rel, unquote=False)
            else:
                target = resolve_doc_path(rel, unquote=False)
            if target is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Arxiu no trobat"})
                return
            try:
                if req_path == "/api/open":
                    os.startfile(str(target))
                else:
                    subprocess.Popen(["explorer.exe", f"/select,{target}"])
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if req_path == "/api/copy-to-personal":
            body = self._read_json_body()
            rel = body.get("path") if body else None
            if not isinstance(rel, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Falta el camp path"})
                return
            source = resolve_doc_path(rel, unquote=False)
            if source is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Arxiu d'Atenea no trobat"})
                return
            subj_id = build_index.resolve_subject_meta(rel.split("/")[0])[0]
            p_cfg = build_index.load_personal_sources()
            entry = p_cfg.get("subjects", {}).get(subj_id)
            if not entry:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "No hi ha carpeta personal configurada per a aquesta assignatura"},
                )
                return
            dest_dir = entry["dir"].resolve()
            dest = (dest_dir / source.name).resolve()
            if not dest.is_relative_to(dest_dir):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Ruta de destí invàlida"})
                return
            try:
                if dest.exists():
                    existed = True
                else:
                    existed = False
                    shutil.copy2(source, dest)
                    os.chmod(dest, dest.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)
                with catalog_lock:
                    build_index.write_catalog()
                if body.get("open", True):
                    os.startfile(str(dest))
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "existed": existed,
                    "destName": dest.name,
                    "destPath": str(dest),
                    "favKey": f"personal:{subj_id}/{dest.name}",
                },
            )
            return

        if req_path == "/api/ui-state":
            body = self._read_json_body()
            fs = body.get("folderStates") if body else None
            if not isinstance(fs, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Falta el camp folderStates"})
                return
            try:
                save_ui_state(fs)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if req_path == "/api/sync":
            with sync_lock:
                if sync_state["running"]:
                    self._send_json(HTTPStatus.OK, {"ok": True, "alreadyRunning": True})
                    return
                sync_state["running"] = True
                sync_state["startedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
                sync_state["finishedAt"] = None
                sync_state["exitCode"] = None
                sync_state["summary"] = None
            t = threading.Thread(target=run_sync_worker, daemon=True)
            t.start()
            self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "started": True})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Endpoint desconegut"})


def ping_existing(port: int) -> bool:
    """Comprova si ja hi ha una instància d'atenea-local escoltant al port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.6) as resp:
            data = json.load(resp)
            return bool(isinstance(data, dict) and data.get("ok") and data.get("app") == "atenea-local")
    except Exception:
        return False


def create_server(preferred_port: int) -> tuple[ThreadingHTTPServer, int]:
    """Enllaça un servidor ThreadingHTTPServer només a 127.0.0.1."""
    for candidate in range(preferred_port, preferred_port + 21):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", candidate), AteneaHandler)
            return srv, candidate
        except OSError:
            continue
    srv = ThreadingHTTPServer(("127.0.0.1", 0), AteneaHandler)
    return srv, srv.server_address[1]


def main() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    parser = argparse.ArgumentParser(description="Servidor local d'Atenea")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if ping_existing(args.port):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"[Atenea] Ja hi ha una instància activa a {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    with catalog_lock:
        build_index.write_catalog()

    server, port = create_server(args.port)
    url = f"http://127.0.0.1:{port}/"
    print(f"[Atenea] Servidor actiu a {url}")
    print("[Atenea] Prem Ctrl+C per aturar el servidor.")

    if not args.no_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Atenea] Aturant servidor...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
