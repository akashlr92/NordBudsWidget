# Nord Buds Widget — install guide (for Claude Code)

A Windows desktop widget that controls **OnePlus Nord Buds 4** (and likely other OnePlus / OPPO /
realme earbuds that use the HeyMelody app) without a phone:

- battery for left / right / case, noise control (Noise cancelling / Transparency / Off)
- audio effects: EQ presets (Balanced / Serenade / Bass), 10-band custom EQ, BassWave bass level
- devices list + "disconnect this PC" so a phone can take the earbuds; "Connect to this PC"
- Find my earbuds, game mode, in-ear detection, dual connection
- small card on the desktop (behind apps) that expands when clicked; starts at sign-in

## Get the package

Download the release zip from GitHub:
**<https://github.com/akashlr92/NordBudsWidget/releases/latest>** → `NordBudsWidget-v2.0.zip`.

If you only have this guide, ask the user before downloading. Then download and unzip the release
into a new, empty folder (PowerShell):

```powershell
$dir = Join-Path $env:USERPROFILE 'Downloads\NordBudsWidget-v2.0'
New-Item -ItemType Directory -Force $dir | Out-Null
Invoke-WebRequest 'https://github.com/akashlr92/NordBudsWidget/releases/download/v2.0/NordBudsWidget-v2.0.zip' -OutFile "$dir\NordBudsWidget-v2.0.zip"
Expand-Archive "$dir\NordBudsWidget-v2.0.zip" $dir -Force
Set-Location "$dir\NordBudsWidget"
```

Work from the unzipped `NordBudsWidget` folder for the steps below. A `git clone` of the
repository works too.

## Instructions for Claude Code

You are installing this widget for the user on **this** Windows PC. Work through the steps in
order, report each result, and stop and ask the user if a step fails.

1. **Check the package.** The unzipped `NordBudsWidget` folder must contain: `install.ps1`, `uninstall.ps1`,
   `nordbuds_widget.pyw`, `nordbuds_ui.html`, `bt_audio.py`, `desk_colors.py`, `nordbuds.ico`,
   `nordbuds.png`. Read `install.ps1` before running it. It only touches the current user's
   profile: `%USERPROFILE%\NordBudsWidget`, `%APPDATA%\NordBudsWidget` and three shortcuts.
   No admin rights, registry edits or downloads.

2. **Check the prerequisites:**
   - Windows 10 or 11.
   - **Python 3.9+** with `pythonw.exe`. Check with `py -3 --version` or `python --version`.
     The Microsoft Store "python" stub doesn't count. If Python is missing, **ask the user**
     before installing it, then use `winget install -e --id Python.Python.3.12`.
     The widget uses the standard library only, so there's nothing to pip install.
   - **Microsoft Edge** (preinstalled on Windows) or Google Chrome. The widget's window is an
     Edge "app" window.
   - The earbuds **paired with this PC** in Settings → Bluetooth. If they aren't, ask the user
     to pair them: put the buds in the case with the lid open, hold the case button until the
     light flashes, then use *Add device* on the PC. Turn off Bluetooth on their phone first,
     or the buds reconnect to the phone. The widget can be installed before pairing.

3. **Install** (from this folder):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   ```

   Every line should show `[ok]`. A line starting `INSTALL FAILED:` says what's missing.
   Optional switches: `-NoStartup` (don't start at sign-in), `-NoLaunch`.

4. **Verify:**
   - `%APPDATA%\NordBudsWidget\widget.log` contains `window attached` within ~10 s.
   - A window titled **Nord Buds 4** exists (class `NordBudsWidgetHost`; the Edge page is hosted inside it).
   - Optional: `%APPDATA%\NordBudsWidget\instance.json` has `port` and `token`.
     `GET http://127.0.0.1:<port>/api/state` with header `X-Token: <token>` returns JSON.
     `"connected": true` means the earbuds' control channel is up. `false` is normal while
     the buds are in the closed case or not paired.
   - Ask the user to double-click **Nord Buds Widget** on the Desktop. It should pop up
     expanded in about 4 seconds.

5. **Tell the user how to use it:**
   - The small card sits top-right on the desktop, **behind** open apps (press Win+D to see it).
     Click it to expand. Click elsewhere or press Esc to shrink it. Drag it to move it.
   - Desktop / Start menu shortcut: opens the widget in front. It also starts at sign-in.
   - Uninstall:

     ```powershell
     powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\NordBudsWidget\uninstall.ps1"
     ```

## Troubleshooting (already handled; for reference)

| Symptom | Cause / fix |
|---|---|
| Double-click seems to do nothing | Wait ~4 s. Launching from a shortcut brings the widget to the front. If it was already running, the shortcut just shows it. Check `widget.log`. |
| Shortcut has a blank icon | Explorer refused icons from AppData on the original PC, so the app installs to `%USERPROFILE%\NordBudsWidget` and uses a fresh icon file name each install. Rerun `install.ps1`, then press F5 on the desktop. |
| Widget opens off-screen | A stored position off every monitor is ignored automatically. To reset, delete `%APPDATA%\NordBudsWidget\settings.json`. |
| "Not connected to this PC" | Buds are in the closed case, asleep, or connected elsewhere. Take them out, or press **Connect to this PC** in the widget. |
| `Bluetooth connect failed (WSA 10048)` in the log | Another program (another copy of the widget, or a test script) holds the buds' control channel. Close it. |
| "Earbuds are not answering" | Windows has the buds connected but they send no data. Switch Bluetooth off and on on this PC; if another computer is also connected to the buds, close the widget or HeyMelody there. The widget keeps retrying (up to once a minute); **Refresh** retries at once. |
| The card is an empty dark box | Edge moved its hidden window. The widget repairs this by itself within a second; if not, close it and start it again from the shortcut. |
| Buds pick another phone directly | Not supported: the Nord Buds 4 firmware ignores the "switch device" command. The widget can only disconnect **this PC** (and keeps it off for 2 minutes) so another device can connect. |

## How it works (short)

- `nordbuds_widget.pyw` opens the earbuds' RFCOMM service `0000079A-D102-11E1-9B23-00025B00A5A5`
  (the one HeyMelody uses) and speaks the community reverse-engineered OPPO/OnePlus frame format
  (see the Gadgetbridge and BudsLink projects). It serves `nordbuds_ui.html` on `127.0.0.1` with a
  random port and token, and shows it in an Edge app window hosted inside the widget's own borderless,
  rounded window, kept on the desktop layer.
- `bt_audio.py` connects/disconnects this PC's audio links through the Windows audio stack (no admin).
- `desk_colors.py` samples the screen behind the widget's corners so the rounded corners blend in.
- Settings, log and the widget's own Edge profile: `%APPDATA%\NordBudsWidget`.
