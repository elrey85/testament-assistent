# Clean Your TV

Debloats an Android TV / Google TV over Wi-Fi, backs it up first, asks before touching apps you use, then installs an Apple TV-style launcher. See [PROMPT.md](PROMPT.md) for the original prompt.

## What it does

1. **Connects over Wi-Fi.** It asks for the TV's IP address and connects with `adb connect`.
2. **Identifies the TV.** It reads the brand and model, asks you for the model (`TV model:`), and loads the bloat lists for that brand: Sony, Philips, TCL, Xiaomi, NVIDIA Shield, Chromecast/Google TV, Hisense, Sharp and Toshiba.
3. **Backs up first.** It saves the full package list, the disabled apps, the current home screen and the APKs of every user-installed app to `backups/<model>-<timestamp>/`.
4. **Debloats.**
   - Unused bloat is removed in one batch, after one confirmation.
   - Apps you've used (found in Android usage stats) are asked about one at a time. The default answer is **no**.
   - Core system packages (Settings, Play Store, Play Services, keyboard, WebView and so on) are never touched.
   - Removal uses `pm uninstall -k --user 0`. This hides the app without deleting it, so the change can always be undone.
5. **Installs the launcher.** You choose Projectivy Launcher (the default and closest to the Apple TV look), FLauncher or Wolf Launcher. The script opens the Play Store page on the TV, sets the launcher as the default home screen, and can also hide the stock home screen if you want.

## Usage

```bash
# requires Python 3.8+ and adb (Android platform-tools)
python3 cleanyourtv.py --dry-run     # preview, changes nothing on the TV
python3 cleanyourtv.py               # do it
python3 cleanyourtv.py --restore backups/<folder>   # undo everything
```

On the TV, first turn on developer mode: **Settings → Device Preferences → About → press "Build" 7 times**. Then go to **Developer options** and turn on **USB debugging / Network debugging**. Accept the "Allow debugging?" prompt when it appears.

> Removing the Google Assistant package (`com.google.android.katniss`) stops the voice button on the remote from working. Because it is on the list, the script asks about it if you've used it.
