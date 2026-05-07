import re
import subprocess
import sys


def run(cmd, data=None):
    p = subprocess.run(cmd, input=data, text=True, capture_output=True)
    if p.returncode != 0:
        print("CMD FAILED:", " ".join(cmd))
        print(p.stdout)
        print(p.stderr)
        sys.exit(p.returncode)
    return p.stdout


def read_ref(path, ref):
    return run(["git", "show", f"{ref}:{path}"])


def stage(path, content):
    blob = run(["git", "hash-object", "-w", "--stdin"], data=content).strip()
    run(["git", "update-index", "--add", "--cacheinfo", "100644", blob, path])


base_ref = "86af055"

# Restore full file content from known-good commit and apply intended compat fixes.
dashboard = read_ref("lib/screens/dashboard_tab.dart", base_ref)
dashboard_old = "scrollCacheExtent: ScrollCacheExtent.pixels(1200), physics: const AlwaysScrollableScrollPhysics("
dashboard_new = "cacheExtent: 1200,\n        physics: const AlwaysScrollableScrollPhysics("
if dashboard_old not in dashboard:
    raise SystemExit("dashboard pattern missing in base ref")
dashboard = dashboard.replace(dashboard_old, dashboard_new, 1)
stage("lib/screens/dashboard_tab.dart", dashboard)

details = read_ref("lib/screens/details_tab.dart", base_ref)
if "initialValue: selected," not in details:
    raise SystemExit("details pattern missing in base ref")
details = details.replace("initialValue: selected,", "value: selected,", 1)
stage("lib/screens/details_tab.dart", details)

settings = read_ref("lib/screens/settings_tab.dart", base_ref)
for old, new in [
    ("initialValue: selectedStrategy,", "value: selectedStrategy,"),
    ("initialValue: selectedPreset.id,", "value: selectedPreset.id,"),
]:
    if old not in settings:
        raise SystemExit(f"settings pattern missing in base ref: {old}")
    settings = settings.replace(old, new, 1)
stage("lib/screens/settings_tab.dart", settings)

pubspec = read_ref("pubspec.yaml", "HEAD")
pubspec = re.sub(r"^version:\s*2\.0\.0\+\d+\s*$", "version: 2.0.0+34", pubspec, flags=re.M)
pubspec = re.sub(r"(^\s*msix_version:\s*)2\.0\.0\.\d+\s*$", r"\g<1>2.0.0.34", pubspec, flags=re.M)
stage("pubspec.yaml", pubspec)

print("staged hotfix files")

