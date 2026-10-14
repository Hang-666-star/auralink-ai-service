#!/usr/bin/env python3
"""Create/verify a bounded Guide/Critic source candidate, never deploy it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile

SCHEMA = "AURALINK_R25_AI_CANDIDATE_V1"
ASSISTANT_SCHEMA = "AURALINK_R25_AI_CANDIDATE_V2"
CURRENT_REPOSITORY = "bailangguihun/auralink-ai-service"
REPOSITORY = "Hang-666-star/auralink-ai-service"
REF = "refs/heads/release"
ARCHIVE = "ai-source.zip"
MANIFEST = "ai-release-candidate.json"
SOURCE_PATHS = tuple(sorted([
    "guide_service/__init__.py", "guide_service/__main__.py", "guide_service/app.py",
    "guide_service/config.py", "guide_service/diagnostics.py", "guide_service/env_loader.py",
    "guide_service/qwen_client.py", "guide_service/schema.py", "guide_service/requirements.txt",
    "critic-service/app/__init__.py", "critic-service/app/config.py", "critic-service/app/dimensions.py",
    "critic-service/app/image_utils.py", "critic-service/app/llm.py", "critic-service/app/main.py",
    "critic-service/app/orchestrator.py", "critic-service/app/prompts.py", "critic-service/app/schemas.py",
    "critic-service/requirements.txt",
]))
ASSISTANT_PATHS = tuple(sorted(SOURCE_PATHS + tuple("critic-service/app/assistant/" + name for name in (
    "__init__.py", "config.py", "schemas.py", "router.py", "execution.py", "providers.py", "retrieval.py", "prompts.py"))))


def inventory(schema):
    require(schema in (SCHEMA, ASSISTANT_SCHEMA), "candidate_inventory_version")
    return SOURCE_PATHS if schema == SCHEMA else ASSISTANT_PATHS


DEPENDENCIES = ("guide_service/requirements.txt", "critic-service/requirements.txt")
MAX_FILE = 1024 * 1024
MAX_TOTAL = 5 * MAX_FILE
MAX_ARCHIVE = 6 * MAX_FILE
SECRET_SHAPE = re.compile(rb"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|\bAKIA[A-Z0-9]{16}\b)")


class InvalidCandidate(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise InvalidCandidate(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    def invalid(_):
        raise InvalidCandidate("nonfinite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def no_links(path):
    for part in (path, *path.parents):
        require(not part.is_symlink(), "symlink_refused")


def read_regular(path, maximum):
    no_links(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= maximum, "file_type_or_size")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    require(len(data) <= maximum and len(data) == info.st_size, "file_size_changed")
    return data


def validate_source(path, content):
    require(len(content) <= MAX_FILE and not SECRET_SHAPE.search(content), "source_size_or_secret_shape")
    try:
        text = content.decode("utf-8")
        require("\x00" not in text and "\ufffd" not in text, "source_encoding")
        if path.endswith(".py"):
            ast.parse(text, filename=path, feature_version=(3, 10))
    except (UnicodeError, SyntaxError) as error:
        raise InvalidCandidate("source_encoding_or_syntax") from error


def provenance(repository, ref, commit, run_id, run_attempt, schema=SCHEMA):
    require(repository == (REPOSITORY if schema == SCHEMA else CURRENT_REPOSITORY) and ref == REF, "repository_or_ref")
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "full_commit_required")
    for value in (run_id, run_attempt):
        require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value), "run_identity")
    return {"repository": repository, "ref": ref, "commit_sha": commit,
            "run_id": run_id, "run_attempt": run_attempt}


def collect_source(root, schema=SCHEMA):
    paths = inventory(schema)
    no_links(root)
    source = {}
    for path in paths:
        data = read_regular(root / path, MAX_FILE)
        validate_source(path, data)
        source[path] = data
    require(sum(map(len, source.values())) <= MAX_TOTAL, "total_size")
    # A new runtime module must deliberately update this reviewed contract,
    # not be silently omitted from the candidate while tests use it locally.
    for directory in (root / "guide_service", root / "critic-service" / "app"):
        for path in directory.rglob("*.py"):
            if "__pycache__" not in path.parts:
                require(path.relative_to(root).as_posix() in paths, "unregistered_runtime_module")
    return source


def verify_checkout(root, commit, source):
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        return result.stdout
    require(git("rev-parse", "HEAD").decode().strip() == commit, "checkout_commit_mismatch")
    for path, data in source.items():
        require(git("show", f"{commit}:{path}") == data, "checkout_source_mismatch")


def build(source, identity, schema=SCHEMA):
    paths = inventory(schema)
    require(set(source) == set(paths), "source_inventory")
    provenance(identity["repository"], identity["ref"], identity["commit_sha"], identity["run_id"], identity["run_attempt"], schema)
    files = []
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            data = source[path]
            validate_source(path, data)
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            files.append({"path": path, "bytes": len(data), "sha256": digest(data)})
    blob = target.getvalue()
    require(sum(x["bytes"] for x in files) <= MAX_TOTAL and len(blob) <= MAX_ARCHIVE, "total_size")
    manifest = {"schema": schema, **identity, "python_version": "3.10", "production_approved": False,
        "archive": {"name": ARCHIVE, "bytes": len(blob), "sha256": digest(blob)},
        "files": files, "source_tree_sha256": digest(canonical(files)),
        "dependency_sha256": {path: digest(source[path]) for path in DEPENDENCIES}}
    return manifest, blob


def verify(manifest, blob, expected_commit, expected_run_id, expected_run_attempt):
    require(type(manifest) is dict and set(manifest) == {
        "schema", "repository", "ref", "commit_sha", "run_id", "run_attempt", "python_version",
        "production_approved", "archive", "files", "source_tree_sha256", "dependency_sha256"}, "manifest_fields")
    paths = inventory(manifest["schema"])
    identity = provenance(manifest["repository"], manifest["ref"], manifest["commit_sha"], manifest["run_id"], manifest["run_attempt"], manifest["schema"])
    require(identity["commit_sha"] == expected_commit and identity["run_id"] == expected_run_id
        and identity["run_attempt"] == expected_run_attempt, "unexpected_release_run")
    require(manifest["schema"] in (SCHEMA, ASSISTANT_SCHEMA) and manifest["python_version"] == "3.10"
        and manifest["production_approved"] is False, "manifest_contract")
    require(type(manifest["archive"]) is dict and manifest["archive"] == {
        "name": ARCHIVE, "bytes": len(blob), "sha256": digest(blob)}, "archive_binding")
    require(len(blob) <= MAX_ARCHIVE, "archive_size")
    files = manifest["files"]
    require(type(files) is list and len(files) == len(paths), "file_inventory")
    require(all(type(x) is dict and set(x) == {"path", "bytes", "sha256"} for x in files), "file_fields")
    require([x["path"] for x in files] == list(paths), "file_inventory")
    require(all(type(x["bytes"]) is int and 0 <= x["bytes"] <= MAX_FILE
        and isinstance(x["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", x["sha256"]) for x in files), "file_metadata")
    require(sum(x["bytes"] for x in files) <= MAX_TOTAL, "total_size")
    require(manifest["source_tree_sha256"] == digest(canonical(files)), "source_tree_binding")
    file_map = {x["path"]: x for x in files}
    require(manifest["dependency_sha256"] == {p: file_map[p]["sha256"] for p in DEPENDENCIES}, "dependency_binding")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            require(archive.namelist() == list(paths), "archive_inventory")
            for info in archive.infolist():
                require(not info.is_dir() and not stat.S_ISLNK(info.external_attr >> 16)
                    and info.file_size == file_map[info.filename]["bytes"]
                    and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    and info.flag_bits & 1 == 0, "archive_entry")
                content = archive.read(info)
                require(digest(content) == file_map[info.filename]["sha256"], "file_digest")
                validate_source(info.filename, content)
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise InvalidCandidate("archive_invalid") from error
    return manifest


def write_new(path, data):
    no_links(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--inventory", choices=(SCHEMA, ASSISTANT_SCHEMA), default=ASSISTANT_SCHEMA)
    create.add_argument("--source", type=Path, default=Path("."))
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--ref", required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--run-attempt", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--archive", type=Path, required=True)
    check.add_argument("--expected-commit", required=True)
    check.add_argument("--expected-run-id", required=True)
    check.add_argument("--expected-run-attempt", required=True)
    args = parser.parse_args()
    if args.command == "create":
        identity = provenance(args.repository, args.ref, args.commit, args.run_id, args.run_attempt, args.inventory)
        source = collect_source(args.source, args.inventory)
        verify_checkout(args.source, args.commit, source)
        manifest, blob = build(source, identity, args.inventory)
        verify(manifest, blob, args.commit, args.run_id, args.run_attempt)
        no_links(args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        require(not (args.output / ARCHIVE).exists() and not (args.output / MANIFEST).exists(), "output_exists")
        write_new(args.output / ARCHIVE, blob)
        write_new(args.output / MANIFEST, canonical(manifest) + b"\n")
    else:
        manifest = strict_json(read_regular(args.manifest, MAX_FILE))
        blob = read_regular(args.archive, MAX_ARCHIVE)
        verify(manifest, blob, args.expected_commit, args.expected_run_id, args.expected_run_attempt)
    print(json.dumps({"result": "CANDIDATE_VERIFIED", "commit_sha": manifest["commit_sha"],
        "source_tree_sha256": manifest["source_tree_sha256"], "archive_sha256": manifest["archive"]["sha256"],
        "files": len(manifest["files"]), "production_deployed": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (InvalidCandidate, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("AI_CANDIDATE_REFUSED: " + type(error).__name__) from None
