"""Descarga todos los documentos de Atenea (Moodle UPC) usando la API de web services.

Uso:
    python sync_atenea.py            # sincroniza todas las asignaturas
    python sync_atenea.py --dry-run  # solo muestra lo que descargaría

Configuración en config.json (no se sube a git):
    {"token": "...", "download_videos": true, "max_repo_file_mb": 50}
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://atenea.upc.edu"
ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "Assignatures"
CONFIG_FILE = ROOT / "config.json"
LOG_FILE = ROOT / "sync.log"

# Windows no deja crear rutas de más de 260 caracteres (dejamos margen para ".part")
MAX_PATH_LEN = 245

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp"}
EXCLUDE_BEGIN = "# >>> sync_atenea: archivos grandes (auto) >>>"
EXCLUDE_END = "# <<< sync_atenea <<<"


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def clean(name, maxlen=80):
    """Nombre válido para Windows y no demasiado largo (MAX_PATH)."""
    name = html.unescape(re.sub(r"<[^>]+>", "", name or ""))  # por si viene con HTML
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if len(name) > maxlen:
        stem, ext = os.path.splitext(name)
        name = stem[: maxlen - len(ext)].rstrip(". ") + ext
    return name or "_"


class Moodle:
    def __init__(self, token):
        self.token = token

    def call(self, function, **params):
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json"}
        data.update(flatten(params))
        body = urllib.parse.urlencode(data).encode()
        with urllib.request.urlopen(f"{BASE_URL}/webservice/rest/server.php", body, timeout=60) as r:
            result = json.load(r)
        if isinstance(result, dict) and "exception" in result:
            raise RuntimeError(f"{function}: {result.get('errorcode')} - {result.get('message')}")
        return result

    def download(self, fileurl, dest, timemodified):
        sep = "&" if "?" in fileurl else "?"
        url = f"{fileurl}{sep}token={urllib.parse.quote(self.token)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        os.replace(tmp, dest)
        if timemodified:
            os.utime(dest, (timemodified, timemodified))


def flatten(params, prefix=""):
    """Convierte listas/dicts al formato de parámetros de Moodle (courseids[0]=...)."""
    out = {}
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten(value, name))
        elif isinstance(value, (list, tuple)):
            out.update(flatten(dict(enumerate(value)), name))
        else:
            out[name] = value
    return out


def is_video(filename, mimetype=""):
    return (mimetype or "").startswith("video/") or Path(filename).suffix.lower() in VIDEO_EXTS


def collect_files(moodle, course, assign_attachments):
    """Devuelve una lista de (ruta_relativa, fichero_moodle) para una asignatura."""
    course_dir = Path(clean(re.sub(r"\s*\(Curs Total\)", "", course["fullname"]), 60))
    items = []
    used = set()

    def add(path, f):
        # Acorta el nombre del archivo si la ruta completa es demasiado larga
        excess = len(str(DOCS_DIR / path)) - MAX_PATH_LEN
        if excess > 0:
            stem, ext = os.path.splitext(path.name)
            path = path.with_name(stem[: max(20, len(stem) - excess)].rstrip(". ") + ext)
        # Evita colisiones si dos recursos acaban con la misma ruta
        key = str(path).lower()
        if key in used:
            stem, ext = os.path.splitext(path.name)
            path = path.with_name(f"{stem} ({f.get('_cmid', len(used))}){ext}")
            key = str(path).lower()
        used.add(key)
        items.append((path, f))

    for i, section in enumerate(moodle.call("core_course_get_contents", courseid=course["id"])):
        sec_name = clean(section.get("name") or f"Secció {i}", 45)
        sec_dir = course_dir / f"{i:02d} - {sec_name}"
        for mod in section.get("modules", []):
            if mod.get("visible") == 0 and not mod.get("uservisible", True):
                continue
            modname = mod.get("modname")
            mod_name = clean(mod.get("name", ""), 50)
            files = [c for c in mod.get("contents", []) if c.get("type") == "file"]
            for f in files:
                f["_cmid"] = mod["id"]

            if modname == "resource":
                target = sec_dir if len(files) == 1 else sec_dir / mod_name
                for f in files:
                    add(target / clean(f["filename"]), f)
            elif modname == "folder":
                for f in files:
                    sub = [clean(p, 40) for p in f.get("filepath", "/").strip("/").split("/") if p]
                    add(sec_dir.joinpath(mod_name, *sub) / clean(f["filename"]), f)
            elif modname == "page":
                for f in files:
                    if f["filename"] == "index.html":
                        add(sec_dir / f"{mod_name}.html", f)
            elif modname == "url":
                for c in mod.get("contents", []):
                    if c.get("type") == "url" and c.get("fileurl"):
                        add(sec_dir / f"{mod_name}.url", {"_url": c["fileurl"], "timemodified": c.get("timemodified", 0)})
            elif modname == "assign":
                for f in assign_attachments.get(mod["id"], []):
                    f["_cmid"] = mod["id"]
                    add(sec_dir / mod_name / clean(f["filename"]), f)
            elif files:
                # Otros módulos con ficheros (p. ej. book, imscp...)
                for f in files:
                    sub = [clean(p, 40) for p in f.get("filepath", "/").strip("/").split("/") if p]
                    add(sec_dir.joinpath(mod_name, *sub) / clean(f["filename"]), f)
    return items


def update_large_file_excludes(large_paths):
    """Mantiene la lista de archivos grandes en .git/info/exclude (solo local)."""
    exclude = ROOT / ".git" / "info" / "exclude"
    if not exclude.parent.exists():
        return
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    text = re.sub(rf"\n?{re.escape(EXCLUDE_BEGIN)}.*?{re.escape(EXCLUDE_END)}\n?", "\n", text, flags=re.S)
    lines = ["/" + p.as_posix().replace("[", "\\[").replace("*", "\\*").replace("?", "\\?") for p in sorted(large_paths)]
    block = "\n".join([EXCLUDE_BEGIN, *lines, EXCLUDE_END])
    exclude.write_text(text.rstrip("\n") + "\n" + block + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CONFIG_FILE.exists():
        sys.exit(f"Falta {CONFIG_FILE.name}. Ejecuta primero: python get_token.py")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    download_videos = config.get("download_videos", True)
    max_bytes = config.get("max_repo_file_mb", 50) * 1024 * 1024
    moodle = Moodle(config["token"])

    try:
        site = moodle.call("core_webservice_get_site_info")
    except Exception as e:
        log(f"ERROR: no se puede conectar con Atenea (¿token caducado?): {e}")
        sys.exit(1)
    log(f"Conectado como {site['fullname']}")

    courses = moodle.call("core_enrol_get_users_courses", userid=site["userid"])
    log(f"{len(courses)} asignaturas")

    assign_attachments = {}
    try:
        data = moodle.call("mod_assign_get_assignments", courseids=[c["id"] for c in courses])
        for c in data.get("courses", []):
            for a in c.get("assignments", []):
                assign_attachments[a["cmid"]] = a.get("introattachments", [])
    except Exception as e:
        log(f"Aviso: no se han podido leer los enunciados de tareas: {e}")

    new = updated = skipped_videos = errors = 0
    large = set()
    for course in courses:
        try:
            items = collect_files(moodle, course, assign_attachments)
        except Exception as e:
            log(f"ERROR leyendo '{course['fullname']}': {e}")
            errors += 1
            continue
        for rel, f in items:
            dest = DOCS_DIR / rel
            if "_url" in f:
                content = f"[InternetShortcut]\nURL={f['_url']}\n"
                if not dest.exists() or dest.read_text(encoding="utf-8") != content:
                    if not args.dry_run:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(content, encoding="utf-8")
                    new += 1
                continue

            video = is_video(f["filename"], f.get("mimetype"))
            if video and not download_videos:
                skipped_videos += 1
                continue
            size = f.get("filesize") or 0
            if video or size > max_bytes:
                large.add(Path("Assignatures") / rel)

            tm = f.get("timemodified") or 0
            # Las páginas vienen con filesize 0: en ese caso solo comparamos la fecha
            if dest.exists() and (not size or dest.stat().st_size == size) and int(dest.stat().st_mtime) == tm:
                continue
            existed = dest.exists()
            log(f"{'Actualizando' if existed else 'Nuevo'}: {rel}")
            if args.dry_run:
                new += 1
                continue
            try:
                moodle.download(f["fileurl"], dest, tm)
                if dest.stat().st_size > max_bytes:
                    large.add(Path("Assignatures") / rel)
                updated += existed
                new += not existed
            except Exception as e:
                log(f"ERROR descargando {rel}: {e}")
                errors += 1

    if not args.dry_run:
        update_large_file_excludes(large)
    log(f"Hecho: {new} nuevos, {updated} actualizados, {len(large)} grandes/vídeos solo en local, "
        f"{skipped_videos} vídeos omitidos, {errors} errores")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
