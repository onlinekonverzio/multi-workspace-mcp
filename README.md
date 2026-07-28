# Google Workspace → Claude Code

Claude Code plugin, ami végigvezet a Google Workspace fiókok bekötésén: Gmail, Drive,
Calendar, Docs, Sheets, Slides, Tasks, Contacts. A folyamat során te döntöd el, milyen
szintű hozzáférést adsz és mely szolgáltatásokat kapcsolod be.

## Telepítés

```
/plugin marketplace add onlinekonverzio/multi-workspace-mcp
```

```
/plugin install google-workspace-setup@multi-workspace-mcp
```

## Használat

```
/google-workspace-setup
```

A Claude ezután végigkérdez, és lépésről lépésre elvégzi a beállítást. Kb. 15 perc, ebből a
java a Google Cloud Console-ban töltött idő.

Amire szükséged lesz:

- **uv** — macOS: `brew install uv`, Windows: `winget install --id=astral-sh.uv -e`,
  Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`. A Claude ellenőrzi és szól, ha
  hiányzik. (A Homebrew csak macOS-en és Linuxon van, Windowson nem kell.)
- Hozzáférés a [Google Cloud Console](https://console.cloud.google.com/)-hoz
- Azoknak a Google-fiókoknak a jelszava, amiket be akarsz kötni

> **Mindenki a saját Google Cloud projektjét hozza létre.** Ne használjátok közösen egy
> kolléga projektjét: az ellenőrizetlen projekt élettartamára szól a 100 felhasználós
> korlát, ami nem nullázható, és a publikálási státusz meg a client secret is közös lenne
> - a projekt létrehozása egyébként ingyenes és két kattintás.

## Amit eldöntesz a folyamat során

**Szolgáltatások.** Alapból mind a nyolc, de szűkíthető — kevesebb szolgáltatás rövidebb
hozzájárulási képernyőt és kevesebb betöltött toolt jelent.

**Hozzáférési szint.** Három tipikus felállás:

| | Mit tud a Claude |
|---|---|
| Teljes | Levelet küld, fájlt szerkeszt, eseményt hoz létre a nevedben |
| Csak olvasás | Semmit nem küld, módosít vagy töröl |
| Vegyes | Saját fiók teljes jogú, ügyfélfiókok csak olvasás + Drive fel/letöltés |

A jogosultsági szint a **szerverpéldányhoz** tartozik, nem a fiókhoz: egy példányon minden
fiók ugyanazt kapja. Eltérő szintekhez a Claude több példányt hoz létre, mindegyiket saját
porttal és token-tárolóval — ezt neked nem kell kézzel intézned.

## Amit érdemes előre tudni

**Több domain nem probléma.** Különböző Google Workspace előfizetések fiókjai ugyanabban a
setupban működnek, admin jogosultság nélkül.

**Személyes @gmail.com fiók viszont igen.** A Google az ellenőrizetlen alkalmazásokat
production státuszban keményen tiltja fogyasztói fiókoknál. Megoldható, de ára van: a
projektet Testing státuszba kell tenni, ahol a Google **7 naponta visszavonja az összes
tokent**, tehát hetente újra kell hitelesíteni minden fiókot. A Claude ezt végigbeszéli
veled, mielőtt döntenél.

**A tokenek a gépeden maradnak.** A `~/.google_workspace_mcp/` alatt, `600`-as
jogosultsággal. Semmilyen adat nem megy harmadik félhez — a Claude közvetlenül a Google
API-jait hívja a te hitelesítéseddel.

## Ha elakadsz

A plugin tartalmaz egy hibakeresési referenciát a tipikus hibaüzenetekkel és azok okaival:
[troubleshooting.md](skills/google-workspace-setup/references/troubleshooting.md).
A Claude magától is ide néz, ha valami elhasal.

## Mit használ

A [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp)
szervert `uvx`-en keresztül. Ez a plugin nem tartalmazza a szervert, csak a beállítását
automatizálja.
