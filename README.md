# Atenea Local UI

Sincronitzador automàtic dels documents d'[Atenea (Moodle UPC)](https://atenea.upc.edu) i visor web local en català, 100% offline i sense dependències externes.

### Captures de pantalla

**Vista d'assignatura amb previsualització lateral i desplegables intel·ligents (mode clar)**

![Vista d'assignatura en mode clar](docs/preview-light.png)

**Vista de novetats i estat de sincronització (mode fosc)**

![Vista de novetats en mode fosc](docs/preview-dark.png)

## Funcions principals

- **Sincronització diària (`sync_atenea.py`)**: descarrega apunts, carpetes, enunciats de tasques, pàgines HTML i enllaços `.url` organitzats per assignatura i secció dins d'`Assignatures/`. Si un professor esborra un fitxer d'Atenea, la còpia local es conserva.
- **Índex local (`build_index.py`)**: escaneja `Assignatures/` i analitza `sync.log` per generar `catalog.js` amb tota la informació necessària per a la interfície web.
- **Visor web (`index.html`)**:
  - **Novetats**: fitxers de l'última sincronització, dels darrers 7 dies o 30 dies.
  - **Preferits**: marca fitxers clau amb estrella (`localStorage`).
  - **Cerca global instantània**: sense distinció de majúscules ni accents (`/` o `Ctrl+K`), amb filtres per tipus (`PDF`, `Enllaços`, `Vídeos`, `Pàgines`, `Altres`).
  - **Previsualització integrada**: PDFs en panell lateral, pàgines HTML de Moodle renderitzades en UTF-8 dins d'un `iframe` aïllat, imatges, vídeos, arxius de text/codi i obertura directa dels enllaços `.url`.
  - **Desplegables intel·ligents**: seccions i subcarpetes amb més de 15 arxius plegades per defecte, amb memòria de l'estat obert/tancat.
  - **Tema clar / fosc**: adaptat automàticament al sistema o seleccionable des de la barra superior.
- **Servidor local opcional (`serve.py` + `obrir_atenea.bat`)**:
  - Afegeix botó **Sincronitzar ara** des de la UI.
  - Permet **Obrir al sistema** (Word, Excel, MATLAB, etc.), **Mostrar a la carpeta** a l'Explorador de Windows, reproducció de vídeo amb peticions `Range` i persistència de l'estat dels desplegables a `ui_state.json`.

## Requisits

- Python 3.9 o superior (només llibreria estàndard, no cal instal·lar cap paquet).
- Windows per a `obrir_atenea.bat` i `run_sync.ps1` (els scripts Python funcionen igualment a Linux/macOS).

## Inici ràpid

1. **Obtén el token d'Atenea** (la primera vegada o quan caduqui):
   ```powershell
   python get_token.py
   ```
   Això crea `config.json` en local amb el teu token (no es puja mai a Git).

2. **Sincronitza les assignatures i genera l'índex**:
   ```powershell
   python sync_atenea.py
   python build_index.py
   ```

3. **Obre el visor**:
   - **Mode estàtic**: fes doble clic a `index.html`.
   - **Mode servidor local (recomanat)**: fes doble clic a `obrir_atenea.bat` (o executa `python serve.py`).
   - **Drecera d'escriptori a Windows (sense consola)**: pots crear un accés directe apuntant a `pythonw.exe` amb argument `"C:\ruta\a\serve.py"` i la icona `atenea.ico` inclosa al repositori (`serve.py` també la serveix com a favicon).

## Automatització diària a Windows

Pots crear una tasca al **Programador de tasques (Task Scheduler)** de Windows que executi:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\ruta\a\run_sync.ps1"
```

Si també fas còpia de seguretat dels teus documents en un repositori privat propi, pots passar el paràmetre `-GitPush` a `run_sync.ps1`.

## Personalitzar noms i colors d'assignatures (opcional)

Pots crear un fitxer local `subjects.json` (ignorat per Git) mapejant el codi d'assignatura o el nom de la carpeta a un nom curt i colors clar/fosc:

```json
{
  "240052": ["Termodinàmica", "#D9480F", "#FF8A4C"],
  "240043": {
    "name": "Dinàmica de Sistemes",
    "colorLight": "#C2255C",
    "colorDark": "#F06595"
  }
}
```

## Seguretat i privacitat

- `Assignatures/`, `config.json`, `sync.log`, `catalog.js`, `ui_state.json` i `subjects.json` estan exclosos a `.gitignore`.
- `build_index.py` només llegeix `max_repo_file_mb` de `config.json`; el token no s'inclou mai a `catalog.js`.
- `serve.py` només escolta a `127.0.0.1`, bloqueja el traversal de rutes i l'accés a fitxers fora d'`Assignatures/`, i exigeix validació de capçaleres locals per a les accions `POST /api/*`.
