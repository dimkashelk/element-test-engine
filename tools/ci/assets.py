"""Package only the local executor and Dvizhok; never upload anything here."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = 'script_u_10.0.2_1'
ARCHIVE = 'Dvizhok.xdump'


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def package(output, root=ROOT):
    if not (root / RUNTIME / 'lib').is_dir() or not (root / ARCHIVE).is_file():
        raise ValueError('Required local Script executor or Dvizhok.xdump is missing')
    output.parent.mkdir(parents=True, exist_ok=True)
    def normalize(member):
        if PurePosixPath(member.name).name == '.DS_Store':
            return None
        if not (member.isfile() or member.isdir()):
            raise ValueError('Runtime assets must contain only regular files/directories')
        member.uid = member.gid = 0
        member.uname = member.gname = ''
        member.mode = 0o755 if member.isdir() or member.name.endswith('/executor') else 0o644
        return member
    # Exclusive creation prevents accidental replacement of an existing release bundle.
    with tarfile.open(output, 'x:gz') as bundle:
        for name in (RUNTIME, ARCHIVE):
            bundle.add(root / name, arcname=name, filter=normalize)
    checksum = digest(output)
    output.with_suffix(output.suffix + '.sha256').write_text(checksum + '\n')
    return checksum


def unpack(bundle, expected, root=ROOT):
    if not re.fullmatch('[0-9a-fA-F]{64}', expected or ''):
        raise ValueError('Set CI_ASSETS_SHA256 to the SHA-256 printed by the pack command')
    if digest(bundle) != expected.lower():
        raise ValueError('CI assets checksum mismatch')
    if (root / RUNTIME).exists() or (root / ARCHIVE).exists():
        raise ValueError('Unpack requires a checkout without existing local assets')
    with tarfile.open(bundle, 'r:gz') as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] not in {RUNTIME, ARCHIVE}
                    or (path.parts[0] == ARCHIVE and (len(path.parts) != 1 or not member.isfile()))
                    or not (member.isfile() or member.isdir()) or path in names):
                raise ValueError('Unexpected path or file type in CI assets: ' + member.name)
            names.add(path)
        archive.extractall(root, members=members, filter='data')
    if not (root / RUNTIME / 'lib').is_dir() or not (root / ARCHIVE).is_file():
        raise ValueError('CI assets bundle is incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['pack', 'unpack'])
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--sha256', default='')
    args = parser.parse_args()
    try:
        if args.command == 'pack':
            print('CI_ASSETS_SHA256=' + package(args.bundle))
        else:
            unpack(args.bundle, args.sha256)
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
