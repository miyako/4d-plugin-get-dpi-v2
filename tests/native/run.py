#!/usr/bin/env python3
"""Direct, unsigned macOS regressions; never invokes project build phases."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BASELINE = "b01450430591ec8f158d24557fe148b23eee1682"
DEFECTS = ("path", "bytes", "copy-units", "copy-owners", "gray", "size", "thumbnail",
           "array-rejection")
CONTROLS = ("array", "scalar", "sentinel", "dispatcher")
INTENTIONAL_EDITS = {"README.md", "4D Plugin API/Classes/C_PICTURE.cpp",
                     "4D Plugin API/Classes/ARRAY_TEXT.cpp"}
SDK_SHA256 = "2721c3d4338cc2a350599ffcee2a8008abaa756acfd45dd710720c64ca8c8ce3"


def preserve():
    tracked = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "-z", BASELINE], cwd=ROOT
    ).decode().split("\0")
    for path in filter(None, tracked):
        if path in INTENTIONAL_EDITS:
            continue
        original = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT)
        if (ROOT / path).read_bytes() != original:
            raise SystemExit(f"Unexpected change to preserved baseline file: {path}")
    sdk = ROOT / "4D Plugin API/4DPluginAPI.c"
    if hashlib.sha256(sdk.read_bytes()).hexdigest() != SDK_SHA256:
        raise SystemExit("Full SDK implementation hash mismatch")
    print("PRESERVED: baseline files except the three explicit source/documentation edits; SDK hash verified")


def build(source, output):
    sdk = source / "4D Plugin API"
    subprocess.run(
        ["clang++", "-std=c++11", "-x", "objective-c++", "-fno-objc-arc",
         "-include", str(source / "4D Plugin_Prefix.pch"),
         "-I" + str(sdk), "-I" + str(sdk / "Classes"),
         "-Wno-deprecated-declarations", "-Wno-extern-c-compat",
         "-Wno-shorten-64-to-32", "-g", "-O1", "-fno-omit-frame-pointer",
         "-fsanitize=address,undefined", "-framework", "Cocoa", "-framework", "Carbon",
         str(sdk / "4DPluginAPI.c"), str(source / "4DPlugin.cpp"),
         *map(str, sorted((sdk / "Classes").glob("*.cpp"))),
         str(ROOT / "tests/native/ownership.mm"), "-o", str(output)],
        check=True,
    )


def run(executable, case, empty, mode):
    environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                       UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    return subprocess.run([str(executable), case, empty, mode], env=environment,
                          capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true",
                        help="also prove named failures against immutable git-archive baseline")
    args = parser.parse_args()
    preserve()
    with tempfile.TemporaryDirectory(prefix="dpi-ownership-") as directory:
        work = Path(directory)
        fixed = work / "fixed"
        build(ROOT, fixed)
        total = 0
        for case in DEFECTS + CONTROLS:
            for empty in ("allocated-empty", "null-empty"):
                modes = (("replacement", "unchanged", "failure")
                         if case in DEFECTS and case not in ("bytes", "array-rejection")
                         else ("replacement",))
                for mode in modes:
                    result = run(fixed, case, empty, mode)
                    if result.returncode:
                        raise SystemExit(result.stdout + result.stderr)
                    print(result.stdout.strip())
                    total += 1
        print(f"FIXED: {total} scenarios passed")
        if args.baseline:
            source = work / "baseline"
            source.mkdir()
            archive = work / "baseline.tar"
            subprocess.run(["git", "archive", "--format=tar", "-o", str(archive), BASELINE],
                           cwd=ROOT, check=True)
            subprocess.run(["tar", "-xf", str(archive), "-C", str(source)], check=True)
            baseline = work / "original"
            build(source, baseline)
            for case in DEFECTS:
                result = run(baseline, case, "allocated-empty", "replacement")
                if result.returncode == 0:
                    raise SystemExit(f"Baseline unexpectedly passed {case}")
                diagnostic = ("AddressSanitizer: heap-buffer-overflow" if case == "copy-units"
                              else "FAIL: explicit ownership balances")
                if diagnostic not in result.stderr:
                    raise SystemExit(f"Wrong baseline failure for {case}:\n{result.stderr}")
                print(f"BASELINE FAIL {case}: {diagnostic}")
                print("\n".join(line for line in result.stderr.splitlines()
                                if "strings=" in line or "duplicate string disposal" in line))
            for case in CONTROLS:
                result = run(baseline, case, "allocated-empty", "replacement")
                if result.returncode:
                    raise SystemExit(f"Baseline control failed {case}:\n{result.stderr}")
                print(result.stdout.strip())
            print(f"BASELINE {BASELINE}: all {len(DEFECTS)} named defects fail; four controls pass")


if __name__ == "__main__":
    main()
