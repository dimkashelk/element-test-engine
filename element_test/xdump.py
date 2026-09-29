#!/usr/bin/env python3
"""Extract 1C:Element project sources from an xdump (standard library only)."""

import argparse
import shutil
import stat
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath


MAX_APPLICATION_BYTES = 256 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_FILES = 10_000


def archive_path(name: str) -> PurePosixPath:
    """Reject ambiguous paths before mapping archive names onto the filesystem."""
    name = unicodedata.normalize("NFC", name)
    parts = name.rstrip("/").split("/")
    if "\\" in name or "\0" in name or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise ValueError(f"Небезопасный путь в архиве: {name!r}")
    return PurePosixPath(*parts)


def extract_project(source: Path, output: Path) -> list[Path]:
    """Publish extracted project directories; never overwrite existing projects."""
    with zipfile.ZipFile(source) as dump:
        applications = [i for i in dump.infolist() if i.filename == "application.zip"]
        if len(applications) != 1:
            raise ValueError("xdump должен содержать ровно один application.zip")
        if applications[0].file_size > MAX_APPLICATION_BYTES:
            raise ValueError("application.zip превышает лимит 256 MiB")
        with tempfile.TemporaryFile() as application:
            with dump.open(applications[0]) as stream:
                shutil.copyfileobj(stream, application)
            application.seek(0)
            with zipfile.ZipFile(application) as archive:
                entries = []
                seen = set()
                for info in archive.infolist():
                    if not info.filename.startswith("src/"):
                        continue
                    path = archive_path(info.filename)
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise ValueError(f"Ссылка или специальный файл в исходниках: {path}")
                    if path in seen:
                        raise ValueError(f"Повторяющийся путь: {path}")
                    seen.add(path)
                    entries.append((info, path))
                    if len(entries) > MAX_SOURCE_FILES:
                        raise ValueError("Превышен лимит исходников: 10 000 записей")

                roots = sorted({path.parent for info, path in entries
                                if not info.is_dir() and path.name == "Проект.yaml"})
                if not roots:
                    raise ValueError("В src/ не найден Проект.yaml")
                if any(root == other or root in other.parents
                       for i, root in enumerate(roots) for other in roots[i + 1:]):
                    raise ValueError("Вложенные корни проектов не поддерживаются")
                counts = {root.name.casefold(): sum(other.name.casefold() == root.name.casefold()
                                                    for other in roots) for root in roots}
                names = [root.name if counts[root.name.casefold()] == 1
                         else f"{root.parent.name}__{root.name}" for root in roots]
                if len({name.casefold() for name in names}) != len(names):
                    raise ValueError("В архиве неоднозначные пути проектов после учёта поставщика")
                directory_names = dict(zip(roots, names))

                planned = []
                destinations = set()
                total_bytes = 0
                for info, path in entries:
                    if info.is_dir():
                        continue
                    for root in roots:
                        if root not in path.parents:
                            continue
                        relative = path.relative_to(root)
                        # Compiler assembly is absent from source project exports (.tar).
                        if ".asm" in relative.parts:
                            break
                        destination = PurePosixPath(directory_names[root]) / relative
                        key = str(destination).casefold()
                        if key in destinations:
                            raise ValueError(f"Конфликт имён файлов: {destination}")
                        destinations.add(key)
                        planned.append((info, destination))
                        total_bytes += info.file_size
                        break
                if len(planned) > MAX_SOURCE_FILES or total_bytes > MAX_SOURCE_BYTES:
                    raise ValueError("Превышен лимит исходников: 10 000 файлов / 128 MiB")

                output.mkdir(parents=True, exist_ok=True)
                targets = [output / name for name in names]
                for target in targets:
                    if target.exists() or target.is_symlink():
                        raise ValueError(f"Каталог уже существует: {target}")
                with tempfile.TemporaryDirectory(prefix=".xdump-", dir=output) as staging:
                    stage = Path(staging)
                    for info, destination in planned:
                        target = stage.joinpath(*destination.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as stream, target.open("xb") as file:
                            shutil.copyfileobj(stream, file)
                    for target in targets:
                        (stage / target.name).rename(target)
                return targets


def write_tar(source: Path, projects: list[Path], *, destination: Path | None = None) -> Path:
    """Pack project roots without replacing an existing archive."""
    if destination is None:
        destination = source.with_suffix(".tar")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Архив уже существует: {destination}")
    with tempfile.TemporaryDirectory(prefix=".xdump-tar-", dir=destination.parent) as staging:
        temporary = Path(staging) / "project.tar"
        with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as archive:
            for project in projects:
                archive.add(project, arcname=project.name)
        # Exclusive creation also protects an archive created while we were packing.
        with temporary.open("rb") as stream, destination.open("xb") as output:
            try:
                shutil.copyfileobj(stream, output)
            except BaseException:
                destination.unlink()
                raise
    return destination


def convert_to_tar(source: Path, work: Path) -> Path:
    """Convert a dump inside caller-owned temporary storage."""
    projects = extract_project(source, work / "sources")
    return write_tar(source, projects, destination=work / "project.tar")


def main() -> int:
    parser = argparse.ArgumentParser(description="Извлечь исходники .xdump и создать .tar рядом")
    parser.add_argument("xdump", type=Path, help="Путь к .xdump")
    parser.add_argument("-o", "--output", type=Path, default=Path("result/xdump"),
                        help="Каталог для папок проектов (по умолчанию result/xdump)")
    args = parser.parse_args()
    try:
        destination = args.xdump.with_suffix(".tar")
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Архив уже существует: {destination}")
        projects = extract_project(args.xdump, args.output)
        archive = write_tar(args.xdump, projects)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    for project in projects:
        print(f"Проект извлечён: {project.resolve()}")
    print(f"Архив создан: {archive.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
