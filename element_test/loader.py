"""Discover projects and extract archives without touching student sources."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import tarfile
import zipfile
import stat

from .yaml_io import InputError

MAX_FILES = 10000
MAX_BYTES = 128 * 1024 * 1024
IGNORED = {".git", "__MACOSX", "node_modules", ".venv"}


def files(root):
    """Reject symlinks, including directory links; never follow external inputs."""
    count, size = 0, 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            if path.name in IGNORED:
                continue
            if path.is_symlink():
                raise InputError(f"Символическая ссылка запрещена: {path.name}")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                count += 1
                size += path.stat().st_size
                if count > MAX_FILES or size > MAX_BYTES:
                    raise InputError("Проект превышает лимит числа файлов или размера")
                yield path
            else:
                raise InputError(f"Недопустимый тип файла: {path.name}")


def discover(root):
    roots = [p.parent for p in files(root) if p.name == "Проект.yaml"]
    if len(roots) != 1:
        raise InputError(f"Ожидался один Проект.yaml; найдено: {len(roots)}")
    return roots[0]


def destination(root, name):
    if "\\" in name or "\0" in name:
        raise InputError("Недопустимое имя файла в архиве")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or ":" in name:
        raise InputError(f"Небезопасный путь в архиве: {name}")
    return root.joinpath(*relative.parts)


def extract(source, root):
    seen, count, size = set(), 0, 0
    entries = 0

    def copy(name, declared_size, stream):
        nonlocal count, size
        target = destination(root, name)
        if target in seen:
            raise InputError(f"Повторяющийся путь в архиве: {name}")
        seen.add(target)
        count += 1
        size += declared_size
        if count > MAX_FILES or size > MAX_BYTES:
            raise InputError("Архив превышает лимит числа файлов или размера")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as out:
            copied = 0
            while chunk := stream.read(65536):
                copied += len(chunk)
                if copied > declared_size:
                    raise InputError("Размер файла не соответствует архиву")
                out.write(chunk)
        if copied != declared_size:
            raise InputError("Архив содержит усечённый файл")

    try:
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                for entry in archive.infolist():
                    entries += 1
                    if entries > MAX_FILES:
                        raise InputError("Архив превышает лимит числа записей")
                    destination(root, entry.filename)
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise InputError("Ссылки и специальные файлы в архиве запрещены")
                    if not entry.is_dir():
                        with archive.open(entry) as stream:
                            copy(entry.filename, entry.file_size, stream)
        elif tarfile.is_tarfile(source):
            with tarfile.open(source) as archive:
                for entry in archive:
                    entries += 1
                    if entries > MAX_FILES:
                        raise InputError("Архив превышает лимит числа записей")
                    destination(root, entry.name)
                    if entry.isdir():
                        continue
                    if not entry.isfile():
                        raise InputError("Ссылки и специальные файлы в архиве запрещены")
                    with archive.extractfile(entry) as stream:
                        copy(entry.name, entry.size, stream)
        else:
            raise InputError("Поддерживаются каталог, ZIP, TAR и TAR.GZ")
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError, RuntimeError) as exc:
        raise InputError(f"Ошибка распаковки архива: {exc}") from exc


@contextmanager
def open_project(source):
    source = Path(source).absolute()
    if source.is_symlink():
        raise InputError("Символическая ссылка на проект запрещена")
    if source.is_dir():
        yield discover(source)
    elif source.is_file():
        if source.stat().st_size > MAX_BYTES:
            raise InputError("Входной архив превышает лимит 128 MiB")
        with TemporaryDirectory(prefix="element-test-") as temporary:
            root = Path(temporary)
            extract(source, root)
            yield discover(root)
    else:
        raise InputError(f"Проект не найден: {source}")
