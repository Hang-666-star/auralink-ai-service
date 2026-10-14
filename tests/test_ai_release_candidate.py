"""Candidate integrity checks use only stdlib and synthetic source fixtures."""
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ai_release_candidate.py"
SPEC = importlib.util.spec_from_file_location("ai_release_candidate", SCRIPT)
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.source = {path: (b"VALUE = 1\n" if path.endswith(".py") else b"example-package==1.0\n")
                       for path in candidate.ASSISTANT_PATHS}
        self.identity = candidate.provenance(candidate.CURRENT_REPOSITORY, candidate.REF, "a" * 40, "123", "1", candidate.ASSISTANT_SCHEMA)
        self.manifest, self.archive = candidate.build(self.source, self.identity, candidate.ASSISTANT_SCHEMA)

    def verify(self, manifest=None, archive=None, commit="a" * 40, run="123", attempt="1"):
        return candidate.verify(self.manifest if manifest is None else manifest,
            self.archive if archive is None else archive, commit, run, attempt)

    def rebind_archive(self, rewrite):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.archive)) as old, zipfile.ZipFile(output, "w") as new:
            for item in old.infolist():
                new.writestr(item, old.read(item))
            rewrite(new)
        blob = output.getvalue()
        manifest = copy.deepcopy(self.manifest)
        manifest["archive"] = {"name": candidate.ARCHIVE, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        return manifest, blob

    def test_roundtrip_binds_complete_commit_and_assistant_inventory(self):
        verified = self.verify()
        self.assertEqual(27, len(verified["files"]))
        self.assertEqual(set(candidate.DEPENDENCIES), set(verified["dependency_sha256"]))
        self.assertIs(False, verified["production_approved"])

    def test_v1_legacy_inventory_remains_verifiable(self):
        source = {path: b"# synthetic legacy fixture\n" for path in candidate.SOURCE_PATHS}
        identity = candidate.provenance(candidate.REPOSITORY, candidate.REF, "b" * 40, "124", "1")
        manifest, blob = candidate.build(source, identity)
        self.assertEqual(len(candidate.verify(manifest, blob, "b" * 40, "124", "1")["files"]), 19)
        with self.assertRaises(candidate.InvalidCandidate):
            candidate.build(self.source, identity)

    def test_json_duplicate_and_nonfinite_fields_refused(self):
        for raw in ('{"schema":"first","schema":"second"}', '{"x":NaN}', '{"nested":{"x":1,"x":2}}'):
            with self.subTest(raw=raw), self.assertRaises(candidate.InvalidCandidate):
                candidate.strict_json(raw)

    def test_archive_is_deterministic(self):
        manifest, archive = candidate.build(dict(reversed(list(self.source.items()))), self.identity, candidate.ASSISTANT_SCHEMA)
        self.assertEqual(self.manifest, manifest)
        self.assertEqual(self.archive, archive)

    def test_ref_repository_and_short_commit_refused(self):
        for repo, ref, commit in [("other/repo", candidate.REF, "a" * 40),
                                  (candidate.REPOSITORY, "refs/heads/main", "a" * 40),
                                  (candidate.REPOSITORY, candidate.REF, "a" * 7)]:
            with self.subTest(repo=repo, ref=ref, commit=commit), self.assertRaises(candidate.InvalidCandidate):
                candidate.provenance(repo, ref, commit, "123", "1")

    def test_wrong_commit_run_or_attempt_refused(self):
        for fields in [{"commit": "b" * 40}, {"run": "124"}, {"attempt": "2"}]:
            with self.subTest(fields=fields), self.assertRaises(candidate.InvalidCandidate):
                self.verify(**fields)

    def test_changed_archive_refused(self):
        with self.assertRaises(candidate.InvalidCandidate):
            self.verify(archive=self.archive[:-1] + bytes([self.archive[-1] ^ 1]))

    def test_modified_dependency_digest_refused(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["dependency_sha256"][candidate.DEPENDENCIES[0]] = "0" * 64
        with self.assertRaises(candidate.InvalidCandidate):
            self.verify(manifest=manifest)

    def test_self_approval_and_extra_manifest_hooks_refused(self):
        for field, value in [("production_approved", True), ("deploy_command", "unexpected")]:
            manifest = copy.deepcopy(self.manifest)
            manifest[field] = value
            with self.subTest(field=field), self.assertRaises(candidate.InvalidCandidate):
                self.verify(manifest=manifest)

    def test_unlisted_env_legacy_service_and_traversal_refused(self):
        for path in [".env", "musicgen-service/app.py", "../outside.py"]:
            manifest, archive = self.rebind_archive(lambda z: z.writestr(path, "fixture"))
            with self.subTest(path=path), self.assertRaises(candidate.InvalidCandidate):
                self.verify(manifest, archive)

    def test_duplicate_archive_member_refused(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            manifest, archive = self.rebind_archive(lambda z: z.writestr(candidate.ASSISTANT_PATHS[0], "fixture"))
        with self.assertRaises(candidate.InvalidCandidate):
            self.verify(manifest, archive)

    def test_source_set_must_be_complete(self):
        source = dict(self.source)
        del source[candidate.ASSISTANT_PATHS[0]]
        with self.assertRaises(candidate.InvalidCandidate):
            candidate.build(source, self.identity, candidate.ASSISTANT_SCHEMA)

    def test_binary_invalid_python_and_secret_shaped_sources_refused(self):
        for data in [b"\xff", b"def invalid(:", b"KEY = 'sk-" + b"x" * 32 + b"'\n"]:
            source = dict(self.source)
            source["guide_service/app.py"] = data
            with self.subTest(data_length=len(data)), self.assertRaises(candidate.InvalidCandidate):
                candidate.build(source, self.identity, candidate.ASSISTANT_SCHEMA)

    def test_unregistered_runtime_module_refused_while_env_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, data in self.source.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (root / "guide_service/.env").write_text("private-fixture")
            self.assertEqual(self.source, candidate.collect_source(root, candidate.ASSISTANT_SCHEMA))
            (root / "guide_service/new_module.py").write_text("VALUE = 2\n")
            with self.assertRaises(candidate.InvalidCandidate):
                candidate.collect_source(root, candidate.ASSISTANT_SCHEMA)

    def test_checkout_must_match_commit_and_each_git_blob(self):
        class Result:
            def __init__(self, data): self.stdout = data
        def output(command, **kwargs):
            if command[-2:] == ["rev-parse", "HEAD"]:
                return Result(("a" * 40 + "\n").encode())
            return Result(self.source[command[-1].split(":", 1)[1]])
        with patch.object(candidate.subprocess, "run", side_effect=output):
            candidate.verify_checkout(Path("."), "a" * 40, self.source)
            with self.assertRaises(candidate.InvalidCandidate):
                candidate.verify_checkout(Path("."), "b" * 40, self.source)
            changed = dict(self.source)
            changed["guide_service/app.py"] = b"VALUE = 2\n"
            with self.assertRaises(candidate.InvalidCandidate):
                candidate.verify_checkout(Path("."), "a" * 40, changed)

    def test_release_workflow_binds_transferred_repository_and_keeps_writer_gated(self):
        workflow = (SCRIPT.parents[1] / ".github/workflows/release-candidate.yml").read_text()
        # Packaging and the existing publisher must use one canonical transferred
        # identity. A stale owner comparison would refuse the approved artifact.
        self.assertIn('test "$GITHUB_REPOSITORY" = ' + candidate.CURRENT_REPOSITORY, workflow)
        self.assertIn('[[ "$GITHUB_REPOSITORY" == ' + candidate.CURRENT_REPOSITORY + ' ]]', workflow)
        self.assertEqual(2, workflow.count('https://github.com/' + candidate.CURRENT_REPOSITORY + '.git'))
        self.assertNotIn(candidate.REPOSITORY, workflow)
        publish = workflow.split('  publish:', 1)[1]
        self.assertIn("if: vars.R25_DEPLOY_ENABLED == 'true'", publish)
        self.assertIn('environment: production', publish)
        self.assertIn('StrictHostKeyChecking=yes', publish)
        self.assertIn('Refusing an obsolete queued release.', publish)

    def test_existing_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifact"
            candidate.write_new(output, b"old")
            with self.assertRaises(FileExistsError):
                candidate.write_new(output, b"new")
            self.assertEqual(b"old", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
