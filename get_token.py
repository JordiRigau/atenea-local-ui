"""Obtiene un token nuevo de Atenea (login UPC por CAS) y lo guarda en config.json.

Atenea no permite pedir el token con usuario/contraseña porque el login es por CAS,
así que usamos el mismo flujo que la app móvil de Moodle (launch.php). Con el
parámetro "confirmed" Atenea muestra el enlace con el token en la página en lugar
de redirigir directamente a la app.

Los tokens caducan (~2 meses). Cuando pase, sync.log dirá "¿token caducado?".
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

BASE_URL = "https://atenea.upc.edu"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def main():
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    userid = config.get("userid") or input("Tu id de usuario de Atenea (sale en la URL de tu perfil, ?id=...): ").strip()
    passport = random.randint(10**8, 10**9)
    url = (f"{BASE_URL}/admin/tool/mobile/launch.php?service=moodle_mobile_app"
           f"&passport={passport}&urlscheme=moodledownloader&confirmed={userid}")

    print("1) Entra en https://atenea.upc.edu con tu usuario UPC (si no lo estás ya).")
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
    with urllib.request.urlopen(f"{BASE_URL}/webservice/rest/server.php", body, timeout=30) as r:
        info = json.load(r)
    if "exception" in info:
        sys.exit(f"El token no funciona: {info.get('message')}")

    config.update({"token": token, "userid": info["userid"]})
    config.setdefault("download_videos", True)
    config.setdefault("max_repo_file_mb", 50)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\nOK: token guardado en {CONFIG_FILE.name} (usuario: {info['fullname']}).")


if __name__ == "__main__":
    main()
