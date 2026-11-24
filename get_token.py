"""Obtiene un token de Moodle (por defecto Atenea UPC) y lo guarda en config.json.

Funciona con cualquier instancia de Moodle que tenga activados los Moodle Mobile
Web Services (incluidos campus con login SSO/CAS como Atenea). Con el parámetro
"confirmed" Moodle muestra el enlace con el token en la página en lugar de
redirigir directamente a la app móvil.

Los tokens suelen caducar (~2 meses). Cuando pase, sync.log dirá "¿token caducado?".
"""
import base64
import json
import random
import re
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_BASE_URL = "https://atenea.upc.edu"
BASE_URL = DEFAULT_BASE_URL
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def main():
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    raw_url = (
        config.get("base_url")
        or input(f"URL del teu Moodle [{DEFAULT_BASE_URL}]: ").strip()
        or DEFAULT_BASE_URL
    )
    if not raw_url.lower().startswith(("http://", "https://")):
        raw_url = "https://" + raw_url
    base_url = raw_url.rstrip("/")

    userid = config.get("userid") or input("Tu id de usuario en Moodle (sale en la URL de tu perfil, ?id=...): ").strip()
    passport = random.randint(10**8, 10**9)
    url = (f"{base_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app"
           f"&passport={passport}&urlscheme=moodledownloader&confirmed={userid}")

    print(f"1) Entra en {base_url} con tu usuario (si no lo estás ya).")
    input("   Pulsa Enter cuando hayas iniciado sesión...")
    print("\n2) Abro esta página (si no se abre, cópiala en el navegador):\n\n   " + url + "\n")
    webbrowser.open(url)
    print("3) Verás un enlace tipo 'Feu clic aquí per obrir l'app'. Haz clic derecho sobre él")
    print("   -> 'Copiar dirección del enlace' y pégalo aquí.\n")

    raw = input("Pega aquí: ").strip().strip("'\"")
    m = re.search(r"token=([A-Za-z0-9+/=_%-]+)", raw)
    if not m:
        sys.exit("No encuentro 'token=' en lo que has pegado.")
    b64 = urllib.parse.unquote(m.group(1))
    token = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode().split(":::")[1]

    body = urllib.parse.urlencode({"wstoken": token, "wsfunction": "core_webservice_get_site_info",
                                   "moodlewsrestformat": "json"}).encode()
    with urllib.request.urlopen(f"{base_url}/webservice/rest/server.php", body, timeout=30) as r:
        info = json.load(r)
    if "exception" in info:
        sys.exit(f"El token no funciona: {info.get('message')}")

    config.update({"base_url": base_url, "token": token, "userid": info["userid"]})
    config.setdefault("download_videos", True)
    config.setdefault("max_repo_file_mb", 50)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\nOK: token guardado en {CONFIG_FILE.name} (usuario: {info['fullname']}).")


if __name__ == "__main__":
    main()
