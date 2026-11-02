#!/usr/bin/env python3
"""
Clean Your TV
-------------
Debloat an Android TV / Google TV over Wi-Fi, back up first,
ask before touching apps you use, then install an Apple TV-style launcher.

Requirements: Python 3.8+ and `adb` (Android platform-tools) on PATH.
On the TV: Settings > Device Preferences > About > tap "Build" 7x,
then Developer options > enable "USB debugging" / "Network debugging".

Usage:
    python3 cleanyourtv.py              # interactive clean
    python3 cleanyourtv.py --dry-run    # show what would happen, change nothing
    python3 cleanyourtv.py --restore backups/<folder>   # undo a previous run
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Bloat lists. Removal uses `pm uninstall --user 0`, which only hides the app
# for the current user - it stays on the system partition and can always be
# restored with `cmd package install-existing <pkg>` (see --restore).
# ---------------------------------------------------------------------------

COMMON_BLOAT = {
    "com.google.android.tvrecommendations": "Google TV recommendations",
    "com.google.android.leanbacklauncher.recommendations": "Launcher recommendations",
    "com.google.android.katniss": "Google Assistant voice search (breaks voice remote)",
    "com.google.android.youtube.tvmusic": "YouTube Music",
    "com.google.android.play.games": "Google Play Games",
    "com.google.android.videos": "Google Play Movies & TV",
    "com.google.android.tv.remote.service": "Google TV remote service",
    "com.google.android.feedback": "Google feedback",
    "com.google.android.marvin.talkback": "TalkBack",
    "com.netflix.ninja": "Netflix (preinstalled)",
    "com.amazon.amazonvideo.livingroom": "Prime Video (preinstalled)",
    "com.disney.disneyplus": "Disney+ (preinstalled)",
    "com.spotify.tv.android": "Spotify (preinstalled)",
    "com.facebook.katana": "Facebook",
}

BRAND_BLOAT = {
    "sony": {
        "com.sony.dtv.recommendationservice": "Sony recommendations",
        "com.sony.dtv.promos": "Sony promotions",
        "com.sony.dtv.sonyselect": "Sony Select",
        "com.sony.dtv.discovery": "Sony Discover",
        "com.sony.dtv.tvx": "Sony TV Extra",
        "com.sony.dtv.demomode": "Store demo mode",
        "com.sony.dtv.bravialifeapp": "Bravia life",
        "com.sony.dtv.interactivetvutil": "Interactive TV util",
    },
    "philips": {
        "org.droidtv.nettvrecommender": "Philips recommendations",
        "org.droidtv.demome": "Store demo mode",
        "org.droidtv.welcome": "Welcome / promo",
        "org.droidtv.nettvadvert": "Philips ads",
        "org.droidtv.appstore": "Philips app gallery",
    },
    "tcl": {
        "com.tcl.appmarket": "TCL App market",
        "com.tcl.gallery": "TCL Gallery promos",
        "com.tcl.guard": "TCL Guard",
        "com.tcl.waterfall.overseas": "TCL home recommendations",
        "com.tcl.tvweishi": "TCL TV manager",
        "com.tcl.ui_mediaCenter": "TCL media center",
        "com.tcl.xian.StartandroidService": "TCL telemetry",
        "com.tcl.MultiScreenInteraction_TV": "TCL multiscreen",
        "com.tcl.partnercustomizer": "TCL partner customizer",
    },
    "xiaomi": {
        "com.mitv.tvhome.atv": "PatchWall home",
        "com.mitv.videoplayer": "Mi video player",
        "com.mitv.gallery": "Mi gallery",
        "com.xiaomi.mitv.tvmanager": "Mi TV manager",
        "com.mitv.care": "Mi care / feedback",
        "com.xiaomi.mitv.upgrade": "Mi upgrade ads",
    },
    "nvidia": {
        "com.nvidia.tegrazone3": "NVIDIA Games",
        "com.nvidia.ota": "NVIDIA OTA (keep if you want updates)",
        "com.nvidia.feedback": "NVIDIA feedback",
    },
    "chromecast": {
        "com.google.android.apps.tv.launcherx": "Google TV home (replaced by launcher)",
    },
    "hisense": {
        "com.hisense.hiui.launcher": "Hisense promo home",
        "com.jamdeo.tv.vod": "Hisense VOD",
    },
    "sharp": {"com.sharp.tvhome": "Sharp promo home"},
    "toshiba": {"com.toshiba.tvhome": "Toshiba promo home"},
}

# Never touched, regardless of lists.
PROTECTED = {
    "android", "com.android.systemui", "com.android.settings", "com.android.tv.settings",
    "com.android.vending", "com.google.android.gms", "com.google.android.gsf",
    "com.android.packageinstaller", "com.google.android.packageinstaller",
    "com.android.providers.settings", "com.android.inputmethod.latin",
    "com.google.android.inputmethod.latin", "com.google.android.tvkeyboard",
    "com.android.shell", "com.google.android.webview", "com.android.webview",
    "com.android.tv", "com.google.android.tungsten.setupwraith",
}

# Apple TV-style launchers. Installed through the Play Store on the TV,
# so nothing is sideloaded from unknown sources.
LAUNCHERS = {
    "1": ("Projectivy Launcher (closest to Apple TV look, free)", "com.spocky.projengmenu"),
    "2": ("FLauncher (open source, minimal)", "me.efesser.flauncher"),
    "3": ("Wolf Launcher", "com.wolf.firelauncher"),
}

HOME_QUERY = ["cmd", "package", "resolve-activity", "--brief",
              "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class C:
    B = "\033[1m"; D = "\033[2m"; R = "\033[31m"; G = "\033[32m"; Y = "\033[33m"; O = "\033[38;5;209m"; X = "\033[0m"

if not sys.stdout.isatty():
    for k in ("B", "D", "R", "G", "Y", "O", "X"):
        setattr(C, k, "")


def say(msg=""):
    print(msg, flush=True)


def step(n, title):
    say(f"\n{C.O}{C.B}[{n}] {title}{C.X}")


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or (default or "")


def yes(prompt, default=False):
    d = "Y/n" if default else "y/N"
    ans = ask(f"{prompt} ({d})").lower()
    return default if not ans else ans.startswith(("y", "j"))


class ADB:
    def __init__(self, serial, dry_run=False):
        self.serial = serial
        self.dry = dry_run

    def run(self, *args, check=False):
        cmd = ["adb", "-s", self.serial, *args]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout.strip()

    def sh(self, *args):
        return self.run("shell", *args)

    def change(self, *args):
        """Shell command that modifies the TV (skipped in dry-run)."""
        if self.dry:
            say(f"  {C.D}(dry-run) adb shell {' '.join(args)}{C.X}")
            return "Success"
        return self.sh(*args)

    def prop(self, name):
        return self.sh("getprop", name)

    def packages(self, flag=""):
        out = self.sh("pm", "list", "packages", *( [flag] if flag else [] ))
        return sorted(l.split(":", 1)[1] for l in out.splitlines() if l.startswith("package:"))


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def connect():
    if not shutil.which("adb"):
        sys.exit(f"{C.R}adb not found.{C.X} Install Android platform-tools: "
                 "https://developer.android.com/tools/releases/platform-tools")
    step(1, "Connect over Wi-Fi")
    say("On the TV: Settings > Device Preferences > About > tap 'Build' 7x,\n"
        "then Developer options > enable USB/Network debugging. Find the IP under Network.")
    ip = ask("TV IP address (e.g. 192.168.1.42)")
    if not ip:
        sys.exit("No IP given.")
    serial = ip if ":" in ip else f"{ip}:5555"
    out = subprocess.run(["adb", "connect", serial], capture_output=True, text=True).stdout.strip()
    say(f"  {out}")
    say(f"  {C.Y}If the TV shows 'Allow USB debugging?', choose 'Always allow' and press OK.{C.X}")
    for _ in range(3):
        state = subprocess.run(["adb", "-s", serial, "get-state"], capture_output=True, text=True).stdout.strip()
        if state == "device":
            return serial
        ask("Waiting for authorization - press Enter once you've accepted on the TV")
    sys.exit(f"{C.R}Could not connect to {serial}.{C.X} Check that network debugging is on and both devices share a network.")


def identify(adb):
    model = adb.prop("ro.product.model")
    brand = adb.prop("ro.product.brand") or adb.prop("ro.product.manufacturer")
    android = adb.prop("ro.build.version.release")
    say(f"  Detected: {C.B}{brand} {model}{C.X} (Android {android})")
    user_model = ask("TV model", f"{brand} {model}".strip())
    key = (user_model + " " + brand).lower()
    brands = [b for b in BRAND_BLOAT if b in key]
    if "google" in key or "chromecast" in key:
        brands.append("chromecast")
    if "shield" in key:
        brands.append("nvidia")
    return user_model, sorted(set(brands)), int((android or "0").split(".")[0] or 0)


def backup(adb, model):
    step(3, "Back up first")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", model).strip("_") or "tv"
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups", f"{safe}-{stamp}")
    os.makedirs(os.path.join(folder, "apks"), exist_ok=True)

    info = {
        "model": model,
        "serial": adb.serial,
        "created": stamp,
        "android": adb.prop("ro.build.version.release"),
        "fingerprint": adb.prop("ro.build.fingerprint"),
        "home_activity": adb.sh(*HOME_QUERY).splitlines()[-1:] ,
        "packages_all": adb.packages(),
        "packages_disabled": adb.packages("-d"),
        "packages_third_party": adb.packages("-3"),
        "removed": [],
        "installed": [],
    }
    with open(os.path.join(folder, "packages_full.txt"), "w") as f:
        f.write(adb.sh("pm", "list", "packages", "-f", "-u"))

    # Third-party apps are fully deleted on uninstall --user 0 only if they are
    # user apps, so pull their APKs to be able to reinstall them.
    for pkg in info["packages_third_party"]:
        paths = [l.split(":", 1)[1] for l in adb.sh("pm", "path", pkg).splitlines() if l.startswith("package:")]
        if not paths:
            continue
        dest = os.path.join(folder, "apks", pkg)
        os.makedirs(dest, exist_ok=True)
        for p in paths:
            adb.run("pull", p, os.path.join(dest, os.path.basename(p)))

    save_state(folder, info)
    say(f"  {C.G}Backup saved to {folder}{C.X}")
    say(f"  {len(info['packages_all'])} packages listed, {len(info['packages_third_party'])} user APKs pulled.")
    return folder, info


def save_state(folder, info):
    with open(os.path.join(folder, "backup.json"), "w") as f:
        json.dump(info, f, indent=2)


def used_apps(adb):
    """Packages with foreground time recorded in usage stats."""
    out = adb.sh("dumpsys", "usagestats")
    used = {}
    for m in re.finditer(r'package=(\S+)\s+totalTime(?:Used)?="?([^"\s]+)', out):
        pkg, t = m.group(1), m.group(2)
        if t not in ("0", "00:00", "0s", "+0ms") and not t.startswith("00:00:00"):
            used[pkg] = t
    # launchable apps that were opened recently also show "lastTimeUsed" lines
    for m in re.finditer(r'package=(\S+)[^\n]*lastTimeUsed="([^"]+)"', out):
        used.setdefault(m.group(1), "recent")
    return used


def debloat(adb, brands, folder, info):
    step(4, "Debloat (asking before touching apps you use)")
    installed = set(info["packages_all"])
    disabled = set(info["packages_disabled"])
    candidates = dict(COMMON_BLOAT)
    for b in brands:
        candidates.update(BRAND_BLOAT[b])
    candidates = {p: d for p, d in candidates.items()
                  if p in installed and p not in disabled and p not in PROTECTED}

    if not candidates:
        say("  Nothing on the bloat list is installed. Your TV is already clean.")
        return

    used = used_apps(adb)
    quiet = {p: d for p, d in candidates.items() if p not in used}
    loud = {p: d for p, d in candidates.items() if p in used}

    if quiet:
        say(f"\n  {C.B}Unused bloat ({len(quiet)}){C.X} - removed together:")
        for p, d in quiet.items():
            say(f"   - {d:<45} {C.D}{p}{C.X}")
    targets = list(quiet) if quiet and yes("  Remove these", True) else []

    if loud:
        say(f"\n  {C.Y}{C.B}Apps you've used ({len(loud)}){C.X} - asking one by one:")
        for p, d in loud.items():
            if yes(f"   Remove {d} ({p}, used: {used[p]})", False):
                targets.append(p)

    for pkg in targets:
        res = adb.change("pm", "uninstall", "-k", "--user", "0", pkg)
        if "Success" in res:
            info["removed"].append(pkg)
            say(f"   {C.G}✓{C.X} {pkg}")
        else:
            # some firmwares block uninstall for system apps - disabling is the fallback
            res = adb.change("pm", "disable-user", "--user", "0", pkg)
            if "disabled" in res.lower():
                info.setdefault("disabled", []).append(pkg)
                say(f"   {C.Y}~{C.X} {pkg} (disabled instead)")
            else:
                say(f"   {C.R}✗{C.X} {pkg}: {res}")
        save_state(folder, info)


def install_launcher(adb, info, folder):
    step(5, "Install the Apple TV-style launcher")
    for k, (name, pkg) in LAUNCHERS.items():
        say(f"  {k}) {name}")
    choice = ask("  Pick a launcher", "1")
    name, pkg = LAUNCHERS.get(choice, LAUNCHERS["1"])

    if pkg not in adb.packages():
        say(f"  Opening the Play Store page for {name} on your TV - press 'Install' with the remote.")
        adb.change("am", "start", "-a", "android.intent.action.VIEW", "-d", f"market://details?id={pkg}")
        while not adb.dry and pkg not in adb.packages():
            if not yes("  Installed? (press y when the TV says it's done)", True):
                say(f"  {C.Y}Skipping launcher.{C.X}")
                return
        info["installed"].append(pkg)
        save_state(folder, info)

    # Make it the default home screen.
    activity = ""
    for line in adb.sh("cmd", "package", "query-activities", "--brief",
                       "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME").splitlines():
        if line.strip().startswith(pkg + "/"):
            activity = line.strip()
    if activity:
        res = adb.change("cmd", "package", "set-home-activity", activity)
        say(f"  Default home set to {activity}: {res or 'ok'}")
    else:
        say("  Couldn't detect the launcher's home activity; select it when you press Home.")

    if yes("  Also hide the stock home screen so Home always opens the new launcher", False):
        stock = [p for p in ("com.google.android.apps.tv.launcherx", "com.google.android.tvlauncher",
                             "com.google.android.leanbacklauncher", "com.mitv.tvhome.atv",
                             "com.tcl.waterfall.overseas")
                 if p in info["packages_all"] and p not in info["removed"]]
        for p in stock:
            if "disabled" in adb.change("pm", "disable-user", "--user", "0", p).lower() or adb.dry:
                info.setdefault("disabled", []).append(p)
                say(f"   {C.G}✓{C.X} disabled {p}")
        save_state(folder, info)

    adb.change("input", "keyevent", "KEYCODE_HOME")
    say(f"  {C.G}{name} is your home screen now.{C.X}")


def restore(folder):
    path = os.path.join(folder, "backup.json")
    if not os.path.exists(path):
        sys.exit(f"No backup.json in {folder}")
    with open(path) as f:
        info = json.load(f)
    serial = info["serial"]
    subprocess.run(["adb", "connect", serial], capture_output=True)
    adb = ADB(serial)
    say(f"{C.B}Restoring {info['model']} from {folder}{C.X}")
    for pkg in info.get("disabled", []):
        say(f"  enable  {pkg}: {adb.sh('pm', 'enable', '--user', '0', pkg)}")
    for pkg in info.get("removed", []):
        res = adb.sh("cmd", "package", "install-existing", pkg)
        if "installed" not in res.lower():
            apk_dir = os.path.join(folder, "apks", pkg)
            if os.path.isdir(apk_dir):
                apks = [os.path.join(apk_dir, a) for a in sorted(os.listdir(apk_dir))]
                res = adb.run("install-multiple", "-r", *apks) if len(apks) > 1 else adb.run("install", "-r", apks[0])
        say(f"  restore {pkg}: {res}")
    home = (info.get("home_activity") or [""])[0]
    if "/" in home:
        say(f"  home -> {home}: {adb.sh('cmd', 'package', 'set-home-activity', home) or 'ok'}")
    say(f"{C.G}Done. Installed launchers ({', '.join(info.get('installed', [])) or 'none'}) were left in place.{C.X}")


def main():
    ap = argparse.ArgumentParser(description="Clean Your TV - debloat an Android/Google TV over Wi-Fi.")
    ap.add_argument("--dry-run", action="store_true", help="show actions without changing the TV")
    ap.add_argument("--restore", metavar="BACKUP_DIR", help="undo a previous run from its backup folder")
    args = ap.parse_args()

    if args.restore:
        return restore(args.restore)

    say(f"{C.O}{C.B}# Clean Your TV{C.X}")
    say("Debloat my TV over Wi-Fi, back up first,\nask before touching apps I use, then\ninstall the Apple TV-style launcher.")
    if args.dry_run:
        say(f"{C.Y}Dry run: nothing on the TV will be changed (backup is still written).{C.X}")

    serial = connect()
    adb = ADB(serial, dry_run=args.dry_run)

    step(2, "Identify the TV")
    model, brands, _ = identify(adb)
    say(f"  Brand profiles: {', '.join(brands) or 'generic Android TV'}")

    folder, info = backup(adb, model)
    debloat(adb, brands, folder, info)
    install_launcher(adb, info, folder)

    say(f"\n{C.G}{C.B}All done.{C.X} Undo anytime with:\n  python3 {os.path.basename(__file__)} --restore \"{folder}\"")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nAborted. Anything already removed is listed in the backup folder and can be restored.")
