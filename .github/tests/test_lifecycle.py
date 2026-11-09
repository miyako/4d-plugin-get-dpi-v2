import contextlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("lifecycle", ROOT / ".github/scripts/lifecycle.py")
lc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lc)
SHA = "a" * 40


@contextlib.contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def fixture(version="2.3.4"):
    lc.PROJECT.parent.mkdir(parents=True, exist_ok=True)
    lc.PROJECT.write_text("\n".join(
        f"{key} = {version};" for key in lc.KEYS for _ in range(2)
    ))
    lc.PLIST.write_bytes(plistlib.dumps({
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleInfoDictionaryVersion": "6.0",
    }))


def release(tag="v8.7.6", draft=False, identifier=1):
    return {"id": identifier, "tag_name": tag, "draft": draft, "prerelease": False}


def upload(path):
    return {"id": 99, "name": path, "state": "uploaded", "size": Path(path).stat().st_size}


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = working_directory(self.temp.name)
        self.cwd.__enter__()
        self.addCleanup(self.cwd.__exit__, None, None, None)
        self.env = patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"})
        self.env.start()
        self.addCleanup(self.env.stop)
        fixture()


class SourceTests(TemporaryTest):
    def test_explicit_semver_bumps(self):
        for mode, expected in (("patch", "2.3.5"), ("minor", "2.4.0"), ("major", "3.0.0")):
            with self.subTest(mode=mode):
                fixture()
                self.assertEqual(lc.bump(mode), expected)
                self.assertEqual(lc.source_version(), expected)
                self.assertEqual(lc.PROJECT.read_text().count(expected), 4)
                self.assertEqual(plistlib.loads(lc.PLIST.read_bytes())["CFBundleVersion"], expected)

    def test_invalid_and_drift_rejected_before_writing(self):
        for value in ("2.3", "02.3.4", "2.3.4-beta", "garbage", "$(UNKNOWN)", "9.8.7", ""):
            for key in lc.KEYS:
                fixture()
                lc.PROJECT.write_text(lc.PROJECT.read_text().replace(
                    f"{key} = 2.3.4;", f"{key} = {value};", 1))
                before = lc.PROJECT.read_bytes(), lc.PLIST.read_bytes()
                with self.subTest(value=value, key=key), self.assertRaises(ValueError):
                    lc.bump("patch")
                self.assertEqual(before, (lc.PROJECT.read_bytes(), lc.PLIST.read_bytes()))
            for key in ("CFBundleShortVersionString", "CFBundleVersion"):
                fixture()
                data = plistlib.loads(lc.PLIST.read_bytes())
                data[key] = value
                lc.PLIST.write_bytes(plistlib.dumps(data))
                with self.subTest(value=value, key=key), self.assertRaises(ValueError):
                    lc.source_version()

    def test_missing_fields_and_nonstring_values(self):
        for key in lc.KEYS:
            fixture()
            lc.PROJECT.write_text("\n".join(
                line for line in lc.PROJECT.read_text().splitlines() if not line.startswith(key)))
            with self.assertRaises(ValueError):
                lc.source_version()
        for value in (None, 42, True):
            fixture()
            data = plistlib.loads(lc.PLIST.read_bytes())
            if value is None:
                del data["CFBundleShortVersionString"]
            else:
                data["CFBundleShortVersionString"] = value
            lc.PLIST.write_bytes(plistlib.dumps(data))
            with self.assertRaises(ValueError):
                lc.source_version()

    def test_unterminated_assignment_and_duplicate_plist_version(self):
        lc.PROJECT.write_text(lc.PROJECT.read_text() + "\nMARKETING_VERSION = 2.3.4")
        with self.assertRaises(ValueError):
            lc.source_version()
        fixture()
        lc.PLIST.write_text(lc.PLIST.read_text().replace(
            "</dict>", "<key>CFBundleVersion</key><string>2.3.4</string></dict>"))
        with self.assertRaises(ValueError):
            lc.source_version()

    def test_recognized_references_preserved(self):
        for ref in lc.REFERENCES:
            fixture()
            data = plistlib.loads(lc.PLIST.read_bytes())
            data["CFBundleShortVersionString"] = ref
            data["CFBundleVersion"] = ref
            lc.PLIST.write_bytes(plistlib.dumps(data))
            lc.bump("patch")
            data = plistlib.loads(lc.PLIST.read_bytes())
            self.assertEqual(data["CFBundleShortVersionString"], ref)
            self.assertEqual(data["CFBundleVersion"], ref)

    def test_absent_optional_version_and_unrelated_metadata_preserved(self):
        data = plistlib.loads(lc.PLIST.read_bytes())
        del data["CFBundleVersion"]
        lc.PLIST.write_bytes(plistlib.dumps(data))
        lc.bump("patch")
        data = plistlib.loads(lc.PLIST.read_bytes())
        self.assertNotIn("CFBundleVersion", data)
        self.assertEqual(data["CFBundleInfoDictionaryVersion"], "6.0")


class APITests(TemporaryTest):
    def availability(self, pages):
        with patch.object(lc, "remote_refs", return_value=[]), \
             patch.object(lc, "run", return_value=json.dumps(pages)) as command:
            lc.available("v2.3.4")
            self.assertIn("--paginate", command.call_args.args)
            self.assertIn("--slurp", command.call_args.args)
            self.assertEqual(command.call_args.args[-1], "repos/owner/repo/releases?per_page=100")

    def test_all_pages_and_drafts(self):
        self.availability([[release()], [release(identifier=2)], []])
        for draft in (False, True):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.availability([[release()], [release("v2.3.4", draft, 2)]])

    def test_every_page_and_entry_validated(self):
        for pages in (None, {}, [], [None], [[None]], [[{}]], [[release(identifier=True)]],
                      [[release()], {"message": "Not Found"}],
                      [[release()], [release(identifier=1)]],
                      [[release()], [{"id": 2, "tag_name": "", "draft": False, "prerelease": False}]],
                      [[release()], [{"id": 2, "tag_name": "v1", "draft": "false", "prerelease": False}]],
                      [[release("v2.3.4")], [None]]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.availability(pages)

    def test_api_errors_fail_closed(self):
        for message in ("HTTP 404", "HTTP 401", "HTTP 403", "network failure", "page 2 failed"):
            error = subprocess.CalledProcessError(1, ["gh"], stderr=message)
            with patch.object(lc, "remote_refs", return_value=[]), \
                 patch.object(lc, "run", side_effect=error), \
                 self.assertRaises(subprocess.CalledProcessError):
                lc.available("v2.3.4")
        with patch.object(lc, "remote_refs", return_value=[]), \
             patch.object(lc, "run", return_value="not json"), self.assertRaises(ValueError):
            lc.available("v2.3.4")

    def test_existing_remote_tag_blocks_without_api(self):
        with patch.object(lc, "remote_refs", return_value=[SHA]), \
             patch.object(lc, "run") as command, self.assertRaises(ValueError):
            lc.available("v2.3.4")
        command.assert_not_called()

    def test_remote_ref_errors_and_malformed(self):
        for text in ("bad", f"{SHA}\trefs/tags/other\n",
                     f"{SHA}\trefs/tags/v2.3.4\n" * 2):
            with patch.object(lc, "run", return_value=text), self.assertRaises(ValueError):
                lc.remote_refs("refs/tags/v2.3.4")


class PublicationTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.assets = ["DPI-2.3.4.zip", "DPI-2.3.4.dmg"]
        for asset in self.assets:
            Path(asset).write_bytes(b"fixture")

    def commands(self, fail=None, push_status="*"):
        calls = []

        def fake(*args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ("git", "rev-parse"):
                return SHA
            if args[:2] == ("git", "ls-remote"):
                return "" if len([c for c, _ in calls if c[:2] == ("git", "ls-remote")]) == 1 else f"{SHA}\trefs/tags/v2.3.4\n"
            if "--paginate" in args:
                return "[[]]"
            if "--porcelain" in args:
                return f"{push_status}\t{SHA}:refs/tags/v2.3.4\t[new tag]\n"
            if "--method" in args:
                is_upload = args[4].startswith("https://uploads")
                if fail == ("upload" if is_upload else "post"):
                    raise subprocess.CalledProcessError(1, args, stderr="fixture failure")
                if is_upload:
                    return json.dumps(upload(args[-1]))
                return json.dumps(release("v2.3.4", identifier=42))
            raise AssertionError(args)

        return calls, fake

    def test_create_only_exact_sha_and_returned_id_uploads(self):
        calls, fake = self.commands()
        with patch.object(lc, "run", side_effect=fake):
            self.assertEqual(lc.publish(SHA, self.assets), "2.3.4")
        commands = [args for args, _ in calls]
        push = next(c for c in commands if "--porcelain" in c)
        self.assertIn("--no-follow-tags", push)
        self.assertEqual(push[-1], f"{SHA}:refs/tags/v2.3.4")
        post = next((args, kwargs) for args, kwargs in calls
                    if args[:4] == ("gh", "api", "--method", "POST"))
        self.assertEqual(json.loads(post[1]["input"]), {
            "tag_name": "v2.3.4", "target_commitish": SHA, "name": "v2.3.4",
            "draft": False, "prerelease": False,
        })
        self.assertEqual(len([c for c in commands if "/releases/42/assets?" in " ".join(c)]), 2)
        self.assertFalse(any(word in ("DELETE", "PATCH", "--clobber", "--force")
                             for c in commands for word in c))

    def test_post_upload_failure_retains_state_and_never_retries(self):
        for fail in ("post", "upload"):
            calls, fake = self.commands(fail)
            with patch.object(lc, "run", side_effect=fake), \
                 self.assertRaisesRegex(RuntimeError, "retained.*manually"):
                lc.publish(SHA, self.assets)
            commands = [args for args, _ in calls]
            self.assertEqual(len([c for c in commands if "--porcelain" in c]), 1)
            self.assertFalse(any(word in ("DELETE", "PATCH", "--delete", "--clobber")
                                 for c in commands for word in c))
            self.assertEqual(len([c for c in commands if c[:4] == ("gh", "api", "--method", "POST")]),
                             1 if fail == "post" else 2)

    def test_same_sha_race_blocks_post(self):
        calls, fake = self.commands(push_status="=")
        with patch.object(lc, "run", side_effect=fake), self.assertRaises(RuntimeError):
            lc.publish(SHA, self.assets)
        self.assertFalse(any("--method" in c for c, _ in calls))

    def test_failed_tag_target_check_blocks_post(self):
        calls, fake = self.commands()
        with patch.object(lc, "run", side_effect=fake), \
             patch.object(lc, "remote_refs", side_effect=[[], ["b" * 40]]), \
             self.assertRaises(RuntimeError):
            lc.publish(SHA, self.assets)
        self.assertFalse(any("--method" in c for c, _ in calls))

    def test_malformed_post_and_upload_responses_retain_state(self):
        for stage in ("release", "upload"):
            for response in ("{}", "not JSON", "[]"):
                calls, fake = self.commands()

                def malformed(*args, **kwargs):
                    if "--method" in args:
                        is_upload = args[4].startswith("https://uploads")
                        if is_upload == (stage == "upload"):
                            calls.append((args, kwargs))
                            return response
                    return fake(*args, **kwargs)

                with patch.object(lc, "run", side_effect=malformed), \
                     self.assertRaisesRegex(RuntimeError, "retained"):
                    lc.publish(SHA, self.assets)
                self.assertFalse(any(word in ("DELETE", "PATCH") for c, _ in calls for word in c))

    def test_invalid_sha_checkout_and_assets(self):
        with self.assertRaises(ValueError):
            lc.selected_sha("master")
        with patch.object(lc, "run", return_value="b" * 40), self.assertRaises(ValueError):
            lc.selected_sha(SHA)
        for assets in ([], self.assets[::-1], ["missing.zip", "missing.dmg"]):
            with patch.object(lc, "preflight") as preflight, self.assertRaises(ValueError):
                lc.publish(SHA, assets)
            preflight.assert_not_called()

    def test_cli_accepts_assets_after_options(self):
        result = subprocess.run([
            "python3", str(ROOT / ".github/scripts/lifecycle.py"),
            "publish", "--sha", SHA, *self.assets,
        ], text=True, capture_output=True)
        self.assertNotIn("unrecognized arguments", result.stderr)


class GitTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        run = lc.run
        run("git", "init", "--bare", "remote.git")
        run("git", "init", "-b", "selected", "work")
        self.work = Path.cwd() / "work"
        self.remote = Path.cwd() / "remote.git"
        self.gitcwd = working_directory(self.work)
        self.gitcwd.__enter__()
        self.addCleanup(self.gitcwd.__exit__, None, None, None)
        fixture()
        run("git", "config", "user.name", "Fixture")
        run("git", "config", "user.email", "fixture@example.invalid")
        run("git", "config", "push.followTags", "true")
        run("git", "add", ".")
        run("git", "commit", "-m", "fixture")
        run("git", "remote", "add", "origin", str(self.remote))
        run("git", "push", "--no-follow-tags", "origin", "HEAD:refs/heads/selected")
        self.sha = run("git", "rev-parse", "HEAD").strip()
        run("git", "tag", "-a", "unrelated", "-m", "must not follow")

    def advance_remote(self):
        commit = lc.run("git", "commit-tree", "HEAD^{tree}", "-p", self.sha, input="racer\n").strip()
        lc.run("git", "push", "--no-follow-tags", "origin", f"{commit}:refs/heads/selected")
        return commit

    def test_bump_pushes_only_selected_branch_without_follow_tags(self):
        self.assertEqual(lc.bump_branch("patch", self.sha, "refs/heads/selected"), "2.3.5")
        self.assertEqual(lc.remote_refs("refs/heads/selected"),
                         [lc.run("git", "rev-parse", "HEAD").strip()])
        self.assertEqual(lc.run("git", "ls-remote", "--tags", "origin"), "")

    def test_branch_moved_since_dispatch(self):
        self.advance_remote()
        with self.assertRaisesRegex(ValueError, "branch moved"):
            lc.bump_branch("patch", self.sha, "refs/heads/selected")
        self.assertEqual(lc.source_version(), "2.3.4")

    def test_branch_race_after_final_check_is_nonforce_rejected(self):
        original = lc.run
        moved = []

        def racing(*args, **kwargs):
            if args[:2] == ("git", "-c") and "push" in args:
                moved.append(self.advance_remote())
            return original(*args, **kwargs)

        with patch.object(lc, "run", side_effect=racing), self.assertRaises(subprocess.CalledProcessError):
            lc.bump_branch("patch", self.sha, "refs/heads/selected")
        self.assertEqual(lc.remote_refs("refs/heads/selected"), moved)
        self.assertEqual(lc.run("git", "ls-remote", "--tags", "origin"), "")

    def test_branch_race_during_commit_rejected_before_push(self):
        original = lc.run
        moved = []

        def racing(*args, **kwargs):
            result = original(*args, **kwargs)
            if "commit" in args:
                moved.append(self.advance_remote())
            return result

        with patch.object(lc, "run", side_effect=racing), \
             self.assertRaisesRegex(ValueError, "branch moved"):
            lc.bump_branch("patch", self.sha, "refs/heads/selected")
        self.assertEqual(lc.remote_refs("refs/heads/selected"), moved)

    def test_tag_selected_bump_rejected(self):
        with self.assertRaisesRegex(ValueError, "selected branch"):
            lc.bump_branch("patch", self.sha, "refs/tags/v2.3.4")

    def test_real_tag_races_same_and_different_sha(self):
        for same in (True, False):
            tag = "refs/tags/v2.3.4"
            # Each scenario gets its own fresh isolated bare remote.
            remote = self.remote.parent / f"race-{same}.git"
            lc.run("git", "init", "--bare", str(remote))
            lc.run("git", "remote", "set-url", "origin", str(remote))
            for asset in ("DPI-2.3.4.zip", "DPI-2.3.4.dmg"):
                Path(asset).write_bytes(b"fixture")
            original = lc.run
            target = self.sha if same else lc.run(
                "git", "commit-tree", "HEAD^{tree}", "-p", self.sha, input="tag racer\n").strip()

            def racing(*args, **kwargs):
                if args[:2] == ("gh", "api"):
                    if "--paginate" in args:
                        return "[[]]"
                    raise AssertionError("Release POST must not happen after race")
                if "--porcelain" in args:
                    original("git", "push", "--no-follow-tags", "origin", f"{target}:{tag}")
                return original(*args, **kwargs)

            with patch.object(lc, "run", side_effect=racing), self.assertRaises(RuntimeError):
                lc.publish(self.sha, ["DPI-2.3.4.zip", "DPI-2.3.4.dmg"])
            self.assertEqual(lc.remote_refs(tag), [target])
            self.assertEqual(lc.remote_refs("refs/tags/unrelated"), [])

    def test_successful_real_tag_targets_selected_sha_without_follow_tags(self):
        for asset in ("DPI-2.3.4.zip", "DPI-2.3.4.dmg"):
            Path(asset).write_bytes(b"fixture")
        original = lc.run

        def fake_api(*args, **kwargs):
            if args[:2] == ("gh", "api"):
                if args[4].startswith("https://uploads"):
                    return json.dumps(upload(args[-1]))
                return "[[]]" if "--paginate" in args else json.dumps(release("v2.3.4", identifier=42))
            return original(*args, **kwargs)

        with patch.object(lc, "run", side_effect=fake_api):
            lc.publish(self.sha, ["DPI-2.3.4.zip", "DPI-2.3.4.dmg"])
        self.assertEqual(lc.remote_refs("refs/tags/v2.3.4"), [self.sha])
        self.assertEqual(lc.remote_refs("refs/tags/unrelated"), [])


class LiveContractTests(unittest.TestCase):
    def test_live_source_derived_not_hardcoded(self):
        with working_directory(ROOT):
            version = lc.source_version()
            self.assertRegex(version, "^" + lc.VERSION + "$")
            text = lc.PROJECT.read_text()
            for key in lc.KEYS:
                values = re_values(text, key)
                self.assertTrue(values)
                self.assertEqual(set(values), {version})

    def test_workflow_manual_immutable_and_gate_wiring(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("name: Build\n", workflow)
        triggers = workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(triggers.strip(), "workflow_dispatch:")
        self.assertEqual(workflow.count("uses: actions/checkout@v4"), 3)
        self.assertEqual(workflow.count("ref: ${{ github.sha }}"), 3)
        self.assertIn("group: release-publication\n", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("needs: [version, build-windows]", workflow)
        self.assertIn("needs: version", workflow)
        gates = ["preflight --sha", "build x64", "build macOS plugin (universal)",
                 "verify universal binary", "merge Windows binaries", "name: codesign",
                 "package, notarize & staple", "publish --sha"]
        positions = [workflow.index(gate) for gate in gates]
        self.assertEqual(positions, sorted(positions))
        for secret in ("APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
                       "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID",
                       "NOTARYTOOL_PASSWORD"):
            self.assertIn("secrets." + secret, workflow)
        for obsolete in ("inputs.tag", "softprops/action-gh-release", "gh release create"):
            self.assertNotIn(obsolete, workflow)
        bump = (ROOT / ".github/workflows/bump-version.yml").read_text()
        self.assertIn("ref: ${{ github.sha }}", bump)
        self.assertIn('--ref "$SELECTED_REF"', bump)
        self.assertNotIn("git tag", bump)
        helper = (ROOT / ".github/scripts/lifecycle.py").read_text()
        self.assertNotIn('api("PATCH"', helper)
        self.assertNotIn('api("DELETE"', helper)
        self.assertNotIn("--clobber", helper)

    def test_full_suite_survives_temporary_copy_bump(self):
        if os.environ.get("LIFECYCLE_BUMPED_SUITE"):
            return
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            for name in (".github", "DPI.xcodeproj", "Info.plist"):
                source = ROOT / name
                if source.is_dir():
                    shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    shutil.copy2(source, target / name)
            with working_directory(target):
                before = lc.source_version()
                after = lc.bump("patch")
                self.assertNotEqual(before, after)
                result = subprocess.run(
                    ["python3", "-m", "unittest", "discover", "-s", ".github/tests", "-v"],
                    env={**os.environ, "LIFECYCLE_BUMPED_SUITE": "1"},
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def re_values(text, key):
    import re
    return re.findall(r"\b" + key + r"\s*=\s*([^;]*);", text)


if __name__ == "__main__":
    unittest.main()
