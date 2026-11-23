"""Validate bounded criteria snapshots before the engine sees them."""
from pathlib import PurePosixPath
import stat
import zipfile
import yaml
from element_test.assignment import load_assignment
from element_test.yaml_io import InputError, load_yaml
from .common import fingerprint, write_json

FORBIDDEN = {'image','dockerImage','runtimeDirectory','command','env','credentials','callback','callbackUrl','hostPath','mounts','connection','connectionString','dsn','password','docker','service'}


def relative(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value or ':' in value:
        raise InputError('Invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise InputError('Invalid relative path')
    return path


def bounded_yaml(path, max_bytes):
    if path.stat().st_size > max_bytes:
        raise InputError("Criteria YAML size limit")
    text = path.read_text(encoding='utf-8-sig')
    depth,events = 0,0
    for event in yaml.parse(text):
        events += 1
        if isinstance(event,(yaml.events.SequenceStartEvent,yaml.events.MappingStartEvent)):
            depth += 1
        if isinstance(event,(yaml.events.SequenceEndEvent,yaml.events.MappingEndEvent)):
            depth -= 1
        if depth>48 or events>200000 or isinstance(event,yaml.events.AliasEvent) or getattr(event,'anchor',None):
            raise InputError('YAML complexity limit')
    return load_yaml(path, max_bytes=max_bytes)


def prepare_criteria(source, destination, config):
    destination.mkdir(mode=0o2770)
    if source.suffix=='.zip':
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members)>config.get('limits','maxCriteriaFiles'):
                raise InputError('Too many criteria files')
            seen,total = set(),0
            for member in members:
                path = relative(member.filename)
                mode = member.external_attr>>16
                kind = stat.S_IFMT(mode)
                if kind not in (0,stat.S_IFREG,stat.S_IFDIR) or member.flag_bits&1 or str(path) in seen:
                    raise InputError('Unsafe ZIP member')
                seen.add(str(path))
                total += member.file_size
                if total>config.get('limits','maxCriteriaExpandedBytes'):
                    raise InputError('Criteria expansion limit')
                target = destination.joinpath(*path.parts)
                if member.is_dir():
                    target.mkdir(parents=True,exist_ok=True,mode=0o2770)
                    continue
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o2770)
                with archive.open(member) as incoming,target.open('xb') as outgoing:
                    size = 0
                    while block:=incoming.read(65536):
                        size += len(block)
                        if size>member.file_size:
                            raise InputError('ZIP member size mismatch')
                        outgoing.write(block)
    else:
        (destination/'assignment.yaml').write_bytes(source.read_bytes())
    if not (destination/'assignment.yaml').is_file():
        raise InputError('assignment.yaml required at package root')
    for path in destination.rglob('*'):
        if path.is_symlink():
            raise InputError('Symlink criteria')
        if path.suffix in ('.yaml','.yml'):
            data = bounded_yaml(path, config.get('limits','maxCriteriaBytes'))
            if path.name=='assignment.yaml':
                if set(data)-{'name','checks','formRequirements','profile','executionOptions'}:
                    raise InputError('Unknown assignment fields')
                refs = data.get('formRequirements',[])
                for value in refs if isinstance(refs,list) else [refs]:
                    if isinstance(value,str):
                        target = path.parent.joinpath(*relative(value).parts)
                        if not target.is_file() or target.suffix not in ('.yaml','.yml'):
                            raise InputError('Invalid formRequirements reference')
            for check in data.get('checks',[data] if 'id' in data else []):
                if not isinstance(check,dict) or FORBIDDEN.intersection(check):
                    raise InputError('Forbidden execution overrides')
                if check.get('runtimeProfile', '9.0') not in ('9.0','9.3','10.0'):
                    raise InputError('Unsupported runtime profile')
    assignment = load_assignment(destination, max_yaml_bytes=config.get('limits','maxCriteriaBytes'))
    if not isinstance(assignment['name'],str) or not assignment['name'].strip():
        raise InputError('Assignment name must be a nonempty string')
    count=(326 if assignment['profile']['optional'] else 320) if 'profile' in assignment else len(assignment['checks'])
    if count>config.get('limits','maxExpandedChecks'):
        raise InputError('Too many expanded checks')
    write_json(destination/'normalized.json',assignment)
    return fingerprint(assignment)
