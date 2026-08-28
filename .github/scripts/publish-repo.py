#!/usr/bin/env python3
"""Publish and maintain the project's RPM repositories."""

from __future__ import annotations

import argparse
import bz2
import csv
import filecmp
import fnmatch
import gzip
import hashlib
import json
import logging
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, TypeVar
from urllib.parse import unquote, urlparse

LOGGER = logging.getLogger("publish-repo")
PathArgument: TypeAlias = str | os.PathLike[str]
Row: TypeAlias = tuple[str, ...]
Input = TypeVar("Input")
Output = TypeVar("Output")
PACKAGE_KINDS = ("packages", "debuginfo")
MAX_PACKAGE_VERSIONS = 3
MAX_REMOTE_ATTEMPTS = 5
RPM_NAMESPACE = "http://linux.duke.edu/metadata/rpm"
DEFAULT_EXCLUDED_SOURCES = (
    "python-ytmusicapi",
    "ktextaddons",
    "kirigami-app-components",
)
PACKAGE_DIRECTORIES = (Path("plasma"), Path("related"), Path("frameworks"))
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$"
)


class PublishError(RuntimeError):
    """An expected publishing failure with a user-facing message."""


@dataclass(frozen=True, slots=True)
class Config:
    branch: str
    releasever: str
    testing: bool
    logical_repository: str
    debug_repository: str
    normal_repository: str
    github_repository: str
    repository: str
    repository_owner: str
    max_assets_per_release: int
    max_parallel_transfers: int
    excluded_sources: tuple[str, ...]

    @property
    def inventory(self) -> Path:
        return Path("state") / self.logical_repository / "inventory.tsv"

    @property
    def retired_inventory(self) -> Path:
        return self.inventory.with_name("retired.tsv")

    @classmethod
    def from_environment(cls) -> Config:
        names = (
            "branch",
            "releasever",
            "testing",
            "logical_repository",
            "debug_repository",
            "normal_repository",
            "GITHUB_REPOSITORY",
            "REPOSITORY",
            "REPOSITORY_OWNER",
            "MAX_ASSETS_PER_RELEASE",
            "MAX_PARALLEL_TRANSFERS",
        )
        values = {name: require_environment(name) for name in names}
        testing = values["testing"].lower()
        if testing not in {"true", "false"}:
            raise PublishError("testing must be either 'true' or 'false'")
        try:
            maximum = int(values["MAX_ASSETS_PER_RELEASE"])
        except ValueError as error:
            raise PublishError("MAX_ASSETS_PER_RELEASE must be an integer") from error
        if maximum < 1:
            raise PublishError("MAX_ASSETS_PER_RELEASE must be greater than zero")
        try:
            parallel_transfers = int(values["MAX_PARALLEL_TRANSFERS"])
        except ValueError as error:
            raise PublishError("MAX_PARALLEL_TRANSFERS must be an integer") from error
        if parallel_transfers < 1:
            raise PublishError("MAX_PARALLEL_TRANSFERS must be greater than zero")
        if not values["releasever"].isdigit():
            raise PublishError("releasever must be numeric")
        for name in (
            "branch",
            "logical_repository",
            "debug_repository",
            "normal_repository",
            "REPOSITORY",
            "REPOSITORY_OWNER",
        ):
            if not SAFE_COMPONENT.fullmatch(values[name]):
                raise PublishError(f"{name} contains unsafe characters")
        if not SAFE_GITHUB_REPOSITORY.fullmatch(values["GITHUB_REPOSITORY"]):
            raise PublishError("GITHUB_REPOSITORY must have the form owner/repository")
        excluded_sources = tuple(
            pattern
            for pattern in re.split(
                r"[\s,]+",
                os.environ.get("EXCLUDE_SOURCES", ",".join(DEFAULT_EXCLUDED_SOURCES)),
            )
            if pattern
        )
        for pattern in excluded_sources:
            if (
                "/" in pattern
                or "\\" in pattern
                or any(character in pattern for character in "\x00\n\r\t")
            ):
                raise PublishError(f"Invalid excluded source pattern: {pattern!r}")
        return cls(
            branch=values["branch"],
            releasever=values["releasever"],
            testing=testing == "true",
            logical_repository=values["logical_repository"],
            debug_repository=values["debug_repository"],
            normal_repository=values["normal_repository"],
            github_repository=values["GITHUB_REPOSITORY"],
            repository=values["REPOSITORY"],
            repository_owner=values["REPOSITORY_OWNER"],
            max_assets_per_release=maximum,
            max_parallel_transfers=parallel_transfers,
            excluded_sources=excluded_sources,
        )


def require_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise PublishError(f"{name} is required")
    return value


def command(*arguments: PathArgument, capture_output: bool = False) -> str:
    args = [os.fspath(argument) for argument in arguments]
    LOGGER.debug("Running command: %s", " ".join(args))
    result = subprocess.run(
        args,
        check=True,
        encoding="utf-8",
        stdout=subprocess.PIPE if capture_output else None,
    )
    return result.stdout if capture_output else ""


def retry_operation(
    operation: Callable[[], Output],
    description: str,
    attempts: int = MAX_REMOTE_ATTEMPTS,
) -> Output:
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            delay = 2 ** (attempt - 1)
            LOGGER.warning(
                "%s failed (attempt %d/%d); retrying in %d seconds",
                description,
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)
    raise AssertionError("retry loop exited unexpectedly")


def remote_command(*arguments: PathArgument, capture_output: bool = False) -> str:
    return retry_operation(
        lambda: command(*arguments, capture_output=capture_output),
        f"Remote command {' '.join(os.fspath(argument) for argument in arguments[:4])}",
    )


def command_exists(*arguments: PathArgument) -> bool:
    return (
        subprocess.run(
            [os.fspath(argument) for argument in arguments],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def parallel_map(
    function: Callable[[Input], Output],
    items: Iterable[Input],
    max_workers: int,
) -> list[Output]:
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="package-transfer"
    ) as executor:
        return list(executor.map(function, items))


def load_mapping(output: str, description: str) -> dict[str, object]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        raise PublishError(
            f"Invalid JSON returned for {description}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise PublishError(f"Expected a JSON object for {description}")
    return value


def read_tsv(path: Path, columns: int) -> list[Row]:
    if not path.exists():
        return []
    rows: list[Row] = []
    with path.open(encoding="utf-8", newline="") as stream:
        for line_number, row in enumerate(csv.reader(stream, dialect="excel-tab"), 1):
            if not row or row == [""]:
                continue
            if len(row) != columns:
                raise PublishError(
                    f"{path}:{line_number}: expected {columns} fields, found {len(row)}"
                )
            if any(
                any(character in field for character in "\x00\n\r") for field in row
            ):
                raise PublishError(f"{path}:{line_number}: invalid control character")
            rows.append(tuple(row))
    return rows


def write_tsv(
    path: Path, rows: Iterable[Sequence[str]], *, unique: bool = False
) -> None:
    normalized = [tuple(row) for row in rows]
    if unique:
        normalized = sorted(set(normalized))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            csv.writer(stream, dialect="excel-tab", lineterminator="\n").writerows(
                normalized
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def serialize_xml(root: ET.Element) -> bytes:
    if root.tag.startswith("{"):
        namespace = root.tag[1:].partition("}")[0]
        ET.register_namespace("", namespace)
    ET.register_namespace("rpm", RPM_NAMESPACE)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def safe_rpm_name(name: str) -> str:
    safe_name = name.replace("~", "_").replace("^", "_")
    if Path(safe_name).name != safe_name or not safe_name.endswith(".rpm"):
        raise PublishError(f"Unsafe RPM filename: {name!r}")
    return safe_name


def rpm_matches_releasever(path: Path, releasever: str) -> bool:
    release = command("rpm", "-qp", "--qf", "%{RELEASE}", path, capture_output=True)
    return re.search(rf"\.fc{re.escape(releasever)}(?:[._]|$)", release) is not None


def rpm_source_name(path: Path) -> str:
    source_name = command(
        "dnf", "-q", "rq", "--qf", "%{source_name}", path, capture_output=True
    ).strip()
    if not source_name or "\n" in source_name:
        raise PublishError(f"Unable to determine a unique source name for {path}")
    return source_name


def package_is_excluded(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def pull_manifest(item: tuple[Path, str, str]) -> tuple[str, Path]:
    manifests_directory, reference, digest = item
    destination = manifests_directory / digest.removeprefix("sha256:")
    destination.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Downloading package manifest %s", reference)
    remote_command("oras", "pull", reference, "-o", destination)
    return reference, destination


def discover(config: Config) -> None:
    packages = sorted(
        {
            path.name
            for parent in PACKAGE_DIRECTORIES
            if parent.is_dir()
            for path in parent.iterdir()
            if path.is_dir()
        }
    )
    discovered: list[Row] = []
    suffix = re.compile(rf"-{re.escape(config.branch)}-{re.escape(config.releasever)}$")
    latest = f"latest-{config.branch}-{config.releasever}"
    for package_name in packages:
        image = f"ghcr.io/{config.github_repository}/{package_name.lower()}"
        try:
            document = load_mapping(
                remote_command(
                    "oras",
                    "repo",
                    "tags",
                    "--format",
                    "json",
                    image,
                    capture_output=True,
                ),
                f"tags for {image}",
            )
        except subprocess.CalledProcessError:
            LOGGER.warning(
                "Unable to list tags for %s; checking only the latest tag", image
            )
            tags: set[str] = set()
        else:
            raw_tags = document.get("tags", [])
            if not isinstance(raw_tags, list) or not all(
                isinstance(tag, str) for tag in raw_tags
            ):
                raise PublishError(f"Invalid tags returned for {image}")
            tags = {
                tag
                for tag in raw_tags
                if suffix.search(tag) and not tag.startswith(("latest-", "pr-"))
            }
        if command_exists("oras", "manifest", "fetch", f"{image}:{latest}"):
            tags.add(latest)
        for tag in sorted(tags):
            descriptor = load_mapping(
                remote_command(
                    "oras",
                    "manifest",
                    "fetch",
                    "--descriptor",
                    f"{image}:{tag}",
                    capture_output=True,
                ),
                f"descriptor for {image}:{tag}",
            )
            digest = descriptor.get("digest")
            if not isinstance(digest, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", digest
            ):
                raise PublishError(f"Invalid digest returned for {image}:{tag}")
            discovered.append((package_name, f"{image}@{digest}", digest))
    write_tsv(Path("discovered.tsv"), discovered, unique=True)
    known = {
        row[4]
        for path in (config.inventory, config.retired_inventory)
        for row in read_tsv(path, 5)
    }
    write_tsv(
        Path("new-manifests.tsv"),
        (row for row in discovered if row[2] not in known),
        unique=True,
    )


def download(config: Config) -> None:
    manifests = Path("incoming/manifests")
    rpms = Path("incoming/rpms")
    manifests.mkdir(parents=True, exist_ok=True)
    rpms.mkdir(parents=True, exist_ok=True)
    references_by_digest: dict[str, set[str]] = defaultdict(set)
    for _, reference, digest in read_tsv(Path("new-manifests.tsv"), 3):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise PublishError(f"Invalid manifest digest: {digest!r}")
        references_by_digest[digest].add(reference)

    transfers = [
        (manifests, min(references), digest)
        for digest, references in sorted(references_by_digest.items())
    ]
    downloads = parallel_map(
        pull_manifest,
        transfers,
        config.max_parallel_transfers,
    )
    candidates: list[Row] = []
    for reference, destination in downloads:
        digest = f"sha256:{destination.name}"
        for rpm_file in destination.rglob("*.rpm"):
            name = safe_rpm_name(rpm_file.name)
            target = rpms / name
            if target.exists() and not filecmp.cmp(rpm_file, target, shallow=False):
                raise PublishError(f"Conflicting RPM assets have the same name: {name}")
            if not target.exists():
                shutil.copy2(rpm_file, target)
            candidates.append((name, reference, digest))
    write_tsv(Path("incoming/candidates.tsv"), candidates, unique=True)
    known = {
        row[0]
        for path in (config.inventory, config.retired_inventory)
        for row in read_tsv(path, 5)
    }
    stable_names: set[str] = set()
    if config.testing:
        for repository_name in (
            config.normal_repository,
            f"{config.normal_repository}-debuginfo",
        ):
            stable_names.update(
                repository_package_names(Path("repo") / repository_name)
            )
    new_rpms: list[Row] = []
    for name, reference, digest in sorted(set(candidates)):
        rpm_path = rpms / name
        source_name = rpm_source_name(rpm_path)
        if package_is_excluded(source_name, config.excluded_sources):
            LOGGER.info("Skipping %s: source package %s is excluded", name, source_name)
        elif not rpm_matches_releasever(rpm_path, config.releasever):
            LOGGER.info("Skipping %s: not built for Fedora %s", name, config.releasever)
        elif name in stable_names:
            LOGGER.info("Skipping %s: already present in the stable repository", name)
        elif name not in known:
            kind = (
                "debuginfo"
                if "debuginfo-" in name or "debugsource-" in name
                else "packages"
            )
            new_rpms.append((name, kind, reference, digest))
    write_tsv(Path("incoming/new-rpms.tsv"), new_rpms, unique=True)


def assign(config: Config) -> None:
    inventory = read_tsv(config.inventory, 5)
    bucket_history = inventory + read_tsv(config.retired_inventory, 5)
    assignments: list[Row] = []
    new_rpms = read_tsv(Path("incoming/new-rpms.tsv"), 4)
    for kind, repository_name in zip(
        PACKAGE_KINDS, (config.logical_repository, config.debug_repository), strict=True
    ):
        try:
            buckets = [int(row[2]) for row in bucket_history if row[1] == kind]
        except ValueError as error:
            raise PublishError(
                f"Invalid bucket number in {config.inventory}"
            ) from error
        bucket = max(buckets, default=1)
        count = sum(row[1] == kind and int(row[2]) == bucket for row in bucket_history)
        for name, row_kind, reference, digest in new_rpms:
            if row_kind not in PACKAGE_KINDS:
                raise PublishError(f"Unknown package kind: {row_kind!r}")
            if row_kind != kind:
                continue
            if count >= config.max_assets_per_release:
                bucket += 1
                count = 0
            tag = f"{repository_name}-rpm-{bucket:04d}"
            inventory.append((name, kind, str(bucket), tag, digest))
            assignments.append((name, kind, str(bucket), tag, reference))
            count += 1
    write_tsv(config.inventory.with_suffix(".tsv.next"), inventory, unique=True)
    write_tsv(Path("incoming/assignments.tsv"), assignments)


def release_assets(config: Config, tag: str) -> set[str]:
    release = load_mapping(
        remote_command(
            "gh",
            "release",
            "view",
            tag,
            "-R",
            config.github_repository,
            "--json",
            "assets",
            capture_output=True,
        ),
        f"release {tag}",
    )
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PublishError(f"Invalid asset list returned for release {tag}")
    return {
        asset["name"]
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }


def upload_asset(item: tuple[Config, str, str]) -> None:
    config, tag, name = item
    LOGGER.info("Uploading package %s to %s", name, tag)
    for attempt in range(1, MAX_REMOTE_ATTEMPTS + 1):
        try:
            command(
                "gh",
                "release",
                "upload",
                tag,
                Path("incoming/rpms") / safe_rpm_name(name),
                "-R",
                config.github_repository,
            )
            return
        except subprocess.CalledProcessError:
            if name in release_assets(config, tag):
                LOGGER.info("Package %s is already present in %s", name, tag)
                return
            if attempt == MAX_REMOTE_ATTEMPTS:
                raise
            delay = 2 ** (attempt - 1)
            LOGGER.warning(
                "Upload of %s failed (attempt %d/%d); retrying in %d seconds",
                name,
                attempt,
                MAX_REMOTE_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def ensure_release(config: Config, tag: str) -> None:
    if command_exists("gh", "release", "view", tag, "-R", config.github_repository):
        return
    for attempt in range(1, MAX_REMOTE_ATTEMPTS + 1):
        try:
            command(
                "gh",
                "release",
                "create",
                tag,
                "-R",
                config.github_repository,
                "--title",
                tag,
                "--notes",
                f"RPM storage bucket for logical repository {config.logical_repository}.",
            )
            return
        except subprocess.CalledProcessError:
            if command_exists(
                "gh", "release", "view", tag, "-R", config.github_repository
            ):
                return
            if attempt == MAX_REMOTE_ATTEMPTS:
                raise
            delay = 2 ** (attempt - 1)
            LOGGER.warning(
                "Creation of %s failed (attempt %d/%d); retrying in %d seconds",
                tag,
                attempt,
                MAX_REMOTE_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def delete_asset(item: tuple[Config, str, str]) -> None:
    config, tag, name = item
    for attempt in range(1, MAX_REMOTE_ATTEMPTS + 1):
        try:
            command(
                "gh",
                "release",
                "delete-asset",
                tag,
                name,
                "-R",
                config.github_repository,
                "--yes",
            )
            return
        except subprocess.CalledProcessError:
            if name not in release_assets(config, tag):
                LOGGER.info("Testing asset %s is no longer present in %s", name, tag)
                return
            if attempt == MAX_REMOTE_ATTEMPTS:
                raise
            delay = 2 ** (attempt - 1)
            LOGGER.warning(
                "Deletion of %s failed (attempt %d/%d); retrying in %d seconds",
                name,
                attempt,
                MAX_REMOTE_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def upload(config: Config) -> None:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in read_tsv(Path("incoming/assignments.tsv"), 5):
        grouped[row[3]].append(row)
    transfers: list[tuple[Config, str, str]] = []
    for tag, rows in sorted(grouped.items()):
        ensure_release(config, tag)
        existing = release_assets(config, tag)
        for name, _, _, _, _ in rows:
            if name not in existing:
                transfers.append((config, tag, name))
    parallel_map(upload_asset, transfers, config.max_parallel_transfers)
    pending = config.inventory.with_suffix(".tsv.next")
    if not pending.is_file():
        raise PublishError(f"Missing pending inventory: {pending}")
    pending.replace(config.inventory)


def generate_repository(config: Config, kind: str, output: str) -> None:
    repository = Path("repo") / output
    repository.mkdir(parents=True, exist_ok=True)
    sources: list[str] = []
    if (repository / "repodata/repomd.xml").is_file():
        sources.extend(("--repo", str(repository)))
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in read_tsv(Path("incoming/assignments.tsv"), 5):
        if row[1] == kind:
            grouped[row[3]].append(row)
    for tag, rows in sorted(grouped.items()):
        fragment = Path("metadata-fragments") / tag
        shutil.rmtree(fragment, ignore_errors=True)
        fragment.mkdir(parents=True)
        for name, _, _, _, _ in rows:
            source = Path("incoming/rpms") / safe_rpm_name(name)
            if not source.is_file():
                raise PublishError(f"Missing RPM for metadata generation: {source}")
            os.link(source, fragment / name)
        command(
            "createrepo_c",
            "--baseurl",
            f"https://github.com/{config.github_repository}/releases/download/{tag}/",
            fragment,
        )
        sources.extend(("--repo", str(fragment)))
    if not sources:
        command("createrepo_c", repository)
    elif grouped:
        merged = Path(f"merged-{output}")
        shutil.rmtree(merged, ignore_errors=True)
        command("mergerepo_c", "--all", "-o", merged, *sources)
        generated = merged / "repodata"
        if not (generated / "repomd.xml").is_file():
            raise PublishError(f"mergerepo_c did not create metadata for {output}")
        shutil.rmtree(repository / "repodata", ignore_errors=True)
        generated.replace(repository / "repodata")


def repository_package_names(
    repository: Path,
    latest_limit: int | None = None,
    excluded_sources: Sequence[str] = (),
) -> set[str]:
    repo_id = f"retention-{repository.name}"
    arguments: list[PathArgument] = [
        "dnf",
        "-q",
        "--refresh",
        f"--repofrompath={repo_id},{repository}",
        f"--repo={repo_id}",
        "rq",
        "--qf",
        "%{source_name}\\t%{location}\\n",
    ]
    if latest_limit is not None:
        arguments.extend(("--latest-limit", str(latest_limit)))
    output = command(*arguments, capture_output=True)
    names: set[str] = set()
    for line in output.splitlines():
        source_name, separator, location = line.partition("\t")
        if not separator:
            raise PublishError(
                f"Invalid package query output for {repository}: {line!r}"
            )
        if package_is_excluded(source_name, excluded_sources):
            continue
        name = unquote(Path(urlparse(location).path).name)
        if name:
            names.add(safe_rpm_name(name))
    return names


def local_metadata_path(repository: Path, href: str, source: Path) -> Path:
    candidate = repository / href
    try:
        candidate.resolve(strict=True).relative_to(repository.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise PublishError(
            f"Unsafe or missing metadata path in {source}: {href}"
        ) from error
    return candidate


def compress_metadata(path: Path, content: bytes) -> bytes:
    if path.suffix == ".gz":
        return gzip.compress(content, mtime=0)
    if path.suffix == ".bz2":
        return bz2.compress(content)
    if path.suffix == ".xz":
        return lzma.compress(content)
    if path.suffix != ".zst":
        return content
    with tempfile.TemporaryDirectory(prefix="repo-metadata-") as directory:
        source = Path(directory) / "metadata.xml"
        destination = Path(directory) / "metadata.xml.zst"
        source.write_bytes(content)
        command("zstd", "-q", "-f", source, "-o", destination)
        return destination.read_bytes()


def update_repomd_record(
    data: ET.Element, compressed: bytes, uncompressed: bytes
) -> None:
    fields: dict[str, str] = {
        "size": str(len(compressed)),
        "open-size": str(len(uncompressed)),
        "timestamp": str(int(time.time())),
    }
    for field, value in fields.items():
        element = data.find(f"{{*}}{field}")
        if element is not None:
            element.text = value
    for field, payload in (("checksum", compressed), ("open-checksum", uncompressed)):
        element = data.find(f"{{*}}{field}")
        if element is None:
            continue
        algorithm = element.get("type", "sha256")
        try:
            element.text = hashlib.new(algorithm, payload).hexdigest()
        except ValueError as error:
            raise PublishError(
                f"Unsupported repository checksum: {algorithm}"
            ) from error


def retain_repository_packages(repository: Path, retained_names: set[str]) -> None:
    repomd = repository / "repodata/repomd.xml"
    try:
        tree = ET.parse(repomd)
    except (OSError, ET.ParseError) as error:
        raise PublishError(f"Unable to parse {repomd}: {error}") from error
    root = tree.getroot()
    records = {data.get("type"): data for data in root.findall("./{*}data")}
    primary_record = records.get("primary")
    if primary_record is None:
        raise PublishError(f"No primary metadata record in {repomd}")

    retained_package_ids: set[str] = set()
    for metadata_type in ("primary", "filelists", "other"):
        data = records.get(metadata_type)
        if data is None:
            continue
        location = data.find("{*}location")
        href = location.get("href") if location is not None else None
        if not href:
            raise PublishError(f"No location for {metadata_type} metadata in {repomd}")
        path = local_metadata_path(repository, href, repomd)
        content = decompress_metadata(path)
        try:
            metadata_root = ET.fromstring(content)
        except ET.ParseError as error:
            raise PublishError(f"Invalid XML in {path}: {error}") from error
        packages = list(metadata_root.findall("{*}package"))
        for package in packages:
            if metadata_type == "primary":
                rpm_location = package.find("{*}location")
                rpm_href = (
                    rpm_location.get("href") if rpm_location is not None else None
                )
                keep = rpm_href is not None and Path(rpm_href).name in retained_names
                if keep:
                    checksum = package.find("{*}checksum")
                    if checksum is not None and checksum.text:
                        retained_package_ids.add(checksum.text)
            else:
                keep = package.get("pkgid") in retained_package_ids
            if not keep:
                metadata_root.remove(package)
        metadata_root.set("packages", str(len(metadata_root.findall("{*}package"))))
        uncompressed = serialize_xml(metadata_root)
        compressed = compress_metadata(path, uncompressed)
        atomic_write_bytes(path, compressed)
        update_repomd_record(data, compressed, uncompressed)

    for data in list(root.findall("./{*}data")):
        metadata_type = data.get("type", "")
        if not metadata_type.endswith(("_db", "_zck")):
            continue
        location = data.find("{*}location")
        href = location.get("href") if location is not None else None
        if href:
            local_metadata_path(repository, href, repomd).unlink(missing_ok=True)
        root.remove(data)
    atomic_write_bytes(repomd, serialize_xml(root))


def apply_retention(config: Config) -> None:
    retained_names: set[str] = set()
    repositories = (
        (config.logical_repository, config.normal_repository),
        (config.debug_repository, f"{config.normal_repository}-debuginfo"),
    )
    for repository_name, stable_repository_name in repositories:
        repository = Path("repo") / repository_name
        retained = repository_package_names(
            repository,
            MAX_PACKAGE_VERSIONS,
            config.excluded_sources,
        )
        if config.testing:
            stable_names = repository_package_names(
                Path("repo") / stable_repository_name
            )
            duplicates = retained & stable_names
            retained.difference_update(duplicates)
            if duplicates:
                LOGGER.info(
                    "Excluded %d RPMs already present in %s",
                    len(duplicates),
                    stable_repository_name,
                )
        retain_repository_packages(repository, retained)
        retained_names.update(retained)

    inventory = read_tsv(config.inventory, 5)
    active = [row for row in inventory if row[0] in retained_names]
    newly_retired = [row for row in inventory if row[0] not in retained_names]
    retired = read_tsv(config.retired_inventory, 5)
    write_tsv(config.inventory, active, unique=True)
    write_tsv(config.retired_inventory, (*retired, *newly_retired), unique=True)
    if newly_retired:
        LOGGER.info("Retired %d RPM assets", len(newly_retired))


def metadata(config: Config) -> None:
    generate_repository(config, "packages", config.logical_repository)
    generate_repository(config, "debuginfo", config.debug_repository)
    apply_retention(config)
    output = command(
        "dnf",
        "-q",
        f"--repofrompath=logical,repo/{config.logical_repository}",
        "--repo=logical",
        "rq",
        "--qf",
        "%{source_name}-%{version}-%{release}\\n",
        capture_output=True,
    )
    content = "".join(f"{line}\n" for line in sorted(set(output.splitlines())))
    atomic_write_text(
        Path("repo") / config.logical_repository / "packages.txt", content
    )


def create_empty_repository(directory: Path) -> None:
    temporary = directory.with_name(f".{directory.name}.empty")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    try:
        command("createrepo_c", temporary)
        shutil.rmtree(directory, ignore_errors=True)
        temporary.replace(directory)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def prune(config: Config) -> None:
    if config.testing:
        LOGGER.info("Testing publish; no testing assets will be pruned")
        return
    testing_repository = f"{config.normal_repository}-testing"
    inventory_path = Path("state") / testing_repository / "inventory.tsv"
    retired_path = inventory_path.with_name("retired.tsv")
    inventory = read_tsv(inventory_path, 5)
    grouped: dict[str, set[str]] = defaultdict(set)
    for name, _, _, tag, _ in inventory:
        grouped[tag].add(name)
    transfers: list[tuple[Config, str, str]] = []
    for tag, names in sorted(grouped.items()):
        for name in sorted(names & release_assets(config, tag)):
            LOGGER.info("Deleting testing asset %s from %s", name, tag)
            transfers.append((config, tag, name))
    parallel_map(delete_asset, transfers, config.max_parallel_transfers)
    create_empty_repository(Path("repo") / testing_repository)
    create_empty_repository(Path("repo") / f"{testing_repository}-debuginfo")
    atomic_write_text(Path("repo") / testing_repository / "packages.txt", "")
    write_tsv(retired_path, (*read_tsv(retired_path, 5), *inventory), unique=True)
    write_tsv(inventory_path, ())


def repository_entry(
    config: Config, repo_id: str, description: str, enabled: bool
) -> str:
    value = int(enabled)
    return f"""[{config.repository}-github:{repo_id}]
name={config.github_repository} (GitHub) - {description}
baseurl=https://{config.repository_owner}.github.io/{config.repository}/{repo_id}/
type=rpm-md
skip_if_unavailable=True
gpgcheck=1
gpgkey=https://raw.githubusercontent.com/solopashachas/testrpm/refs/heads/unstable/RPM-GPG-KEY-solopashachas
repo_gpgcheck=0
enabled={value}
enabled_metadata={value}
metadata_expire=6h

"""


def repofile(config: Config) -> None:
    definitions = (
        (config.normal_repository, f"{config.branch} Fedora {config.releasever}", True),
        (
            f"{config.normal_repository}-testing",
            f"{config.branch} Fedora {config.releasever} - testing",
            False,
        ),
        (
            f"{config.normal_repository}-debuginfo",
            f"{config.branch} Fedora {config.releasever} - debuginfo",
            False,
        ),
        (
            f"{config.normal_repository}-testing-debuginfo",
            f"{config.branch} Fedora {config.releasever} - testing - debuginfo",
            False,
        ),
    )
    content = "".join(
        repository_entry(config, *definition) for definition in definitions
    )
    destination = (
        Path("repo")
        / config.normal_repository
        / f"{config.repository}-{config.branch}-{config.releasever}.repo"
    )
    atomic_write_text(destination, content)


def primary_metadata_path(repository: Path) -> Path:
    repomd = repository / "repodata/repomd.xml"
    if not repomd.is_file() or repomd.stat().st_size == 0:
        raise PublishError(f"Missing or empty {repomd}")
    try:
        root = ET.parse(repomd).getroot()
    except ET.ParseError as error:
        raise PublishError(f"Invalid XML in {repomd}: {error}") from error
    location = root.find("./{*}data[@type='primary']/{*}location")
    href = location.get("href") if location is not None else None
    if not href:
        raise PublishError(f"No primary metadata location in {repomd}")
    candidate = repository / href
    try:
        candidate.resolve(strict=True).relative_to(repository.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise PublishError(
            f"Unsafe or missing primary metadata path in {repomd}: {href}"
        ) from error
    return candidate


def decompress_metadata(path: Path) -> bytes:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as stream:
                return stream.read()
        if path.suffix == ".bz2":
            with bz2.open(path, "rb") as stream:
                return stream.read()
        if path.suffix == ".xz":
            with lzma.open(path, "rb") as stream:
                return stream.read()
        if path.suffix == ".zst":
            return subprocess.run(
                ["zstd", "-qdc", os.fspath(path)], check=True, stdout=subprocess.PIPE
            ).stdout
        return path.read_bytes()
    except (OSError, EOFError, lzma.LZMAError) as error:
        raise PublishError(
            f"Unable to read primary metadata {path}: {error}"
        ) from error


def validate(config: Config) -> None:
    locations: set[str] = set()
    combined = bytearray()
    for name in (config.logical_repository, config.debug_repository):
        primary = primary_metadata_path(Path("repo") / name)
        content = decompress_metadata(primary)
        combined.extend(content)
        try:
            root = ET.fromstring(content)
        except ET.ParseError as error:
            raise PublishError(f"Invalid XML in {primary}: {error}") from error
        locations.update(
            href
            for element in root.findall(".//{*}location")
            if (href := element.get("href")) is not None
        )
    Path("primary.xml").write_bytes(combined)
    missing = sorted(
        row[0] for row in read_tsv(config.inventory, 5) if row[0] not in locations
    )
    if missing:
        raise PublishError(f"Metadata is missing {', '.join(missing)}")


Stage: TypeAlias = Callable[[Config], None]
STAGES: dict[str, Stage] = {
    "discover": discover,
    "download": download,
    "assign": assign,
    "upload": upload,
    "metadata": metadata,
    "prune": prune,
    "repofile": repofile,
    "validate": validate,
}


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES, help="publishing stage to execute")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.DEBUG if arguments.verbose else logging.INFO,
    )
    try:
        config = Config.from_environment()
        Path("incoming/rpms").mkdir(parents=True, exist_ok=True)
        Path("metadata-fragments").mkdir(parents=True, exist_ok=True)
        config.inventory.parent.mkdir(parents=True, exist_ok=True)
        config.inventory.touch()
        config.retired_inventory.touch()
        LOGGER.info(
            "Running %s stage for %s", arguments.stage, config.logical_repository
        )
        STAGES[arguments.stage](config)
    except PublishError as error:
        LOGGER.error("%s", error)
        return 1
    except subprocess.CalledProcessError as error:
        LOGGER.error(
            "Command failed with exit status %d: %s", error.returncode, error.cmd
        )
        return error.returncode or 1
    except OSError as error:
        LOGGER.error("Operating system error: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
