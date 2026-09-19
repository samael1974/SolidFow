# SolidFlow UX

SolidFlow UX è un'estensione UX per FreeCAD 1.1.x pensata per rendere Sketcher e Part Design più rapidi, contestuali e vicini a un flusso di lavoro CAD moderno.

## Aggiornamento per i tester — 19 settembre 2026

Versione del pacchetto: **0.4.0-beta.13-test.4**. I sorgenti Python e la macro
diagnostica provengono dalla cartella locale SolidFlowUX del manutentore.
Sono aggiornati sketch, features, barra vista e diagnostica. Cache, backup
locali e disinstallatore non fanno parte dei sorgenti distribuiti.

Il setup viene generato dalla procedura GitHub Actions dopo l'aggiornamento.
Scaricare la release **v0.4.0-beta.13-test.4** quando il relativo EXE è disponibile.
Le release precedenti contengono versioni precedenti del programma.

Prima di aggiornare, chiudere FreeCAD e conservare una copia della cartella
SolidFlowUX fuori da `Mod`. La sintassi Python è stata controllata;
questo non sostituisce una prova funzionale dentro FreeCAD.

### Installazione manuale e feedback

Con **Code > Download ZIP**, estrarre e copiare soltanto la sottocartella
`SolidFlowUX` nella cartella `Mod` del profilo FreeCAD utilizzato.
Consultare [le istruzioni di installazione e prova](SolidFlowUX/INSTALLAZIONE.txt).
Questa distribuzione non include una macro di installazione.

Segnalare i problemi nelle [Issues](https://github.com/samael1974/FREECAD-IMPLEMENTAZIONE-UX/issues),
indicando versione FreeCAD, sistema operativo, passaggi, risultato atteso,
risultato ottenuto e rapporto di **SolidFlow > Diagnostica SolidFlow**.
Se il menu non compare, eseguire `SolidFlowUX-Diagnostica.FCMacro`.

## Installazione consigliata

Per l'utente normale non è necessario copiare file, usare PowerShell o conoscere la cartella `Mod`.

1. Aprire **Releases** nella pagina GitHub del progetto.
2. Aprire la release più recente.
3. Scaricare `SolidFlowUX-Setup-v<versione>.exe`.
4. Chiudere completamente FreeCAD.
5. Eseguire il setup e seguire la procedura guidata.

L'installer installa SolidFlow UX nella cartella utente di FreeCAD:

`%APPDATA%\FreeCAD\v1-1\Mod\SolidFlowUX`

## Controllare il codice prima di installare

Chi preferisce verificare personalmente i sorgenti può usare il normale pulsante GitHub **Code > Download ZIP**, oppure scaricare il pacchetto sorgente associato a una Release.

Dopo aver estratto il repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\CHECK_SOURCE.ps1
```

Lo script controlla che il runtime sia completo e prova a compilare sintatticamente tutti i file Python del manifest senza installare nulla.

## Costruire l'EXE sul proprio PC

Prerequisiti:

- Windows 10/11 x64
- Inno Setup 6
- Python 3.11 oppure Python incluso in FreeCAD 1.1, se disponibile

Poi eseguire:

```powershell
powershell -ExecutionPolicy Bypass -File .\BUILD_INSTALLER_LOCAL.ps1
```

Lo script esegue prima `CHECK_SOURCE.ps1`, quindi compila `installer\SolidFlowUX.iss` e genera l'EXE nella cartella `dist` insieme al relativo SHA-256.

## Struttura repository

- `SolidFlowUX/` — runtime corrente installato dall'EXE
- `installer/` — progetto Inno Setup e manutenzione pre-installazione
- `.github/workflows/` — CI e build automatica Windows
- `docs/` — roadmap, ricerca e documentazione corrente
- `archive/` — vecchi layer, vecchi updater e test storici; **non fanno parte del runtime corrente**

## Nota sulle vecchie beta

I file storici non più caricati dal runtime vengono conservati in `archive/` per audit e confronto, ma non vengono installati nelle release correnti.
