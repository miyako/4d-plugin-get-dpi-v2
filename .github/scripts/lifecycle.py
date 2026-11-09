"""Source-only version bumps and create-only release publication."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys


PROJECT = Path("DPI.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
REFERENCES = {
    "$(MARKETING_VERSION)": "MARKETING_VERSION",
    "${MARKETING_VERSION}": "MARKETING_VERSION",
    "$(CURRENT_PROJECT_VERSION)": "CURRENT_PROJECT_VERSION",
    "${CURRENT_PROJECT_VERSION}": "CURRENT_PROJECT_VERSION",
}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def source_version():
    text = PROJECT.read_text()
    values = {}
    for key in KEYS:
        found = re.findall(r"\b" + key + r"\s*=\s*([^;]*);", text)
        assignments = re.findall(r"\b" + key + r"\s*=", text)
        if (not found or len(found) != len(assignments) or
            any(not re.fullmatch(VERSION, v.strip()) for v in found)):
            raise ValueError(f"{PROJECT}: missing or invalid {key}")
        values[key] = {v.strip() for v in found}
    if len(set.union(*values.values())) != 1:
        raise ValueError("Xcode source versions have drifted")
    current = next(iter(values[KEYS[0]]))
    plist = PLIST.read_text()
    data = plistlib.loads(plist.encode())
    if not isinstance(data, dict):
        raise ValueError("Source plist must be a dictionary")
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key not in data and key == "CFBundleVersion":
            continue
        if len(re.findall(r"<key>" + key + r"</key>", plist)) != 1:
            raise ValueError(f"{PLIST}: expected exactly one {key}")
        value = data.get(key)
        if not isinstance(value, str) or (
            value not in REFERENCES and
            (not re.fullmatch(VERSION, value) or value != current)
        ):
            raise ValueError(f"{PLIST}: invalid or drifted {key}: {value!r}")
    return current


def bump(mode):
    current = source_version()
    numbers = list(map(int, current.split(".")))
    index = ("major", "minor", "patch").index(mode)
    numbers[index] += 1
    numbers[index + 1:] = [0] * (2 - index)
    new = ".".join(map(str, numbers))
    text = PROJECT.read_text()
    for key in KEYS:
        text = re.sub(r"(\b" + key + r"\s*=\s*)" + VERSION + r";",
                      lambda m: m[1] + new + ";", text)
    data = plistlib.loads(PLIST.read_bytes())
    plist = PLIST.read_text()
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key in data and data[key] not in REFERENCES:
            pattern = r"(<key>" + key + r"</key>\s*<string>)[^<]*(</string>)"
            plist, count = re.subn(pattern, lambda m: m[1] + new + m[2], plist)
            if count != 1:
                raise ValueError(f"Expected exactly one {key} in source plist")
    PROJECT.write_text(text)
    PLIST.write_text(plist)
    if source_version() != new:
        raise ValueError("Bumped source validation failed")
    return new


def selected_sha(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected immutable selected 40-character commit SHA")
    if run("git", "rev-parse", "HEAD").strip() != sha:
        raise ValueError("Checkout does not match selected github.sha")


def remote_refs(ref):
    output = run("git", "ls-remote", "--refs", "origin", ref)
    rows = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]) or parts[1] != ref:
            raise ValueError("Malformed remote ref response")
        rows.append(parts[0])
    if len(rows) > 1:
        raise ValueError("Duplicate remote ref")
    return rows


def bump_branch(mode, sha, ref):
    selected_sha(sha)
    if not ref.startswith("refs/heads/"):
        raise ValueError("Bump version requires a selected branch, not a tag")
    run("git", "check-ref-format", ref)
    if run("git", "status", "--porcelain").strip():
        raise ValueError("Bump requires a clean checkout")
    if remote_refs(ref) != [sha]:
        raise ValueError("Selected branch moved; dispatch Bump version again")
    new = bump(mode)
    run("git", "add", "--", str(PROJECT), str(PLIST))
    run("git", "-c", "user.name=github-actions[bot]",
        "-c", "user.email=github-actions[bot]@users.noreply.github.com",
        "commit", "-m", f"chore: bump version to {new}")
    if remote_refs(ref) != [sha]:
        raise ValueError("Selected branch moved; refusing bump push")
    run("git", "-c", "push.followTags=false", "push", "--no-follow-tags",
        "origin", f"HEAD:{ref}")
    return new


def repository():
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    return repo


def api(method, endpoint, payload=None):
    args = ["gh", "api", "--method", method, endpoint]
    if payload is not None:
        args += ["--input", "-"]
    return json.loads(run(*args, input=json.dumps(payload) if payload is not None else None))


def available(tag):
    if remote_refs(f"refs/tags/{tag}"):
        raise ValueError(f"Remote tag {tag} already exists")
    # --slurp preserves page boundaries, including empty pages; gh follows Link.
    pages = json.loads(run("gh", "api", "--paginate", "--slurp",
                           f"repos/{repository()}/releases?per_page=100"))
    if not isinstance(pages, list) or not pages:
        raise ValueError("Malformed or missing release pages")
    tags = []
    ids = set()
    for page in pages:
        if not isinstance(page, list):
            raise ValueError("Malformed release page")
        for release in page:
            if (not isinstance(release, dict) or
                type(release.get("id")) is not int or release["id"] <= 0 or
                not isinstance(release.get("tag_name"), str) or not release["tag_name"] or
                type(release.get("draft")) is not bool or
                type(release.get("prerelease")) is not bool or release["id"] in ids):
                raise ValueError("Malformed or duplicate release entry")
            ids.add(release["id"])
            tags.append(release["tag_name"])
    if tag in tags:
        raise ValueError(f"Release {tag} already exists (including drafts)")


def preflight(sha):
    selected_sha(sha)
    version = source_version()
    available(f"v{version}")
    return version


def publish(sha, assets):
    version = source_version()
    expected = [f"DPI-{version}.zip", f"DPI-{version}.dmg"]
    if assets != expected or any(not Path(p).is_file() or Path(p).stat().st_size == 0 for p in assets):
        raise ValueError(f"Expected nonempty assets: {expected}")
    # All native build/sign/notary gates precede this final check.
    preflight(sha)
    tag = f"v{version}"
    try:
        result = run("git", "-c", "push.followTags=false", "push", "--porcelain",
                     "--no-follow-tags", "origin", f"{sha}:refs/tags/{tag}")
        if not any(line.startswith("*\t") and
                   line.split("\t")[1] == f"{sha}:refs/tags/{tag}"
                   for line in result.splitlines()):
            raise ValueError("Tag was not newly created; possible same-SHA creation race")
        if remote_refs(f"refs/tags/{tag}") != [sha]:
            raise ValueError("Remote tag target differs from selected SHA")
        release = api("POST", f"repos/{repository()}/releases", {
            "tag_name": tag,
            "target_commitish": sha,
            "name": tag,
            "draft": False,
            "prerelease": False,
        })
        if (not isinstance(release, dict) or type(release.get("id")) is not int or
            release["id"] <= 0 or release.get("tag_name") != tag or
            release.get("draft") is not False or release.get("prerelease") is not False):
            raise ValueError("Malformed new release response")
        for path in assets:
            uploaded = json.loads(run("gh", "api", "--method", "POST",
                f"https://uploads.github.com/repos/{repository()}/releases/{release['id']}/assets?name={path}",
                "-H", "Content-Type: application/octet-stream", "--input", path))
            if (not isinstance(uploaded, dict) or type(uploaded.get("id")) is not int or
                uploaded["id"] <= 0 or uploaded.get("name") != path or
                uploaded.get("state") != "uploaded" or
                type(uploaded.get("size")) is not int or
                uploaded["size"] != Path(path).stat().st_size):
                raise ValueError(f"Malformed or incomplete uploaded asset response: {path}")
    except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Publication failed. Partial remote tag/release/assets are retained. "
            "Inspect remote state and recover manually; do not retry this version."
        ) from error
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("version", "bump", "preflight", "publish"))
    parser.add_argument("--mode", choices=("patch", "minor", "major"))
    parser.add_argument("--sha")
    parser.add_argument("--ref")
    parser.add_argument("assets", nargs="*")
    args = parser.parse_args()
    if args.command == "version":
        result = source_version()
    elif args.command == "bump":
        if not args.mode or not args.sha or not args.ref:
            parser.error("bump requires --mode, --sha and --ref")
        result = bump_branch(args.mode, args.sha, args.ref)
    elif args.command == "preflight":
        result = preflight(args.sha or "")
    else:
        result = publish(args.sha or "", args.assets)
    print(result)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"version={result}\ntag=v{result}\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
