"""Read-only source comparison and fresh planning evidence for Dvizhok dumps."""
from collections import Counter
from hashlib import sha256
import difflib
import json
from pathlib import Path

from element_test.loader import open_project
from element_test.model import analyze
from element_test.coverage import inventory
from element_test.capability_map import build_map
from element_test.branches import inventory as branch_inventory

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'result/dvizhok-dumps-assessment-2026-10-14'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    assignments = sorted(p.parent for p in (REPO / 'assignments').rglob('assignment.yaml'))
    sources = {}
    summary = {}
    for name in ('Dvizhok-2.xdump', 'Dvizhok-dev-final.xdump'):
        archive = REPO / name
        before = sha256(archive.read_bytes()).hexdigest()
        dest = OUT / archive.stem
        with open_project(archive) as root:
            model = analyze(root)
            files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
                     if p.is_file() and p.suffix in {'.yaml', '.xbsl', '.xbql'}}
            sources[name] = files
            for relative, content in files.items():
                target = dest / 'sources' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            save(dest / 'model.json', model)
            cov = inventory(root, model, assignments)
            save(dest / 'coverage.json', cov)
            caps = build_map(root, model, assignments, name, method_inventory=cov)
            caps['archiveSha256'] = before
            save(dest / 'capability-map.json', caps)
            branches = branch_inventory(root, model)
            save(dest / 'branches.json', branches)
            summary[name] = {
                'archiveSha256': before, 'sourceHash': model['sourceHash'],
                'sourceCompatibility': model['compatibilityVersion'],
                'sourceFiles': len(files), 'fileTypes': dict(Counter(Path(p).suffix for p in files)),
                'elementTypes': dict(Counter(e['elementType'] for e in model['elements'])),
                'diagnostics': model['diagnostics'], 'coverage': cov['counts'],
                'capabilities': caps['counts'],
                'branches': {k: v for k, v in branches.items() if k not in {'records'}},
                'unplannedMethods': [{**m['identity'], 'reason': m['nextContract']}
                                     for m in cov['methods'] if m['status'] != 'PLAN_PREPARED'],
                'queryPlanning': [{ 'source': i['source'], 'stages': i['stages']}
                                   for i in caps['items'] if i['kind'] == 'source-query']}
        assert sha256(archive.read_bytes()).hexdigest() == before
        print(name, json.dumps({k: summary[name][k] for k in
              ('archiveSha256', 'sourceHash', 'sourceFiles', 'coverage', 'capabilities')}, ensure_ascii=False), flush=True)
    old, new = sources.values()
    diff = {'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()),
            'changed': sorted(p for p in old.keys() & new.keys() if old[p] != new[p]),
            'identical': sum(old[p] == new[p] for p in old.keys() & new.keys())}
    save(OUT / 'diff.json', diff)
    patches = []
    for path in diff['changed'] + diff['added'] + diff['removed']:
        patches.extend(difflib.unified_diff(old.get(path, b'').decode('utf-8-sig').splitlines(True),
                       new.get(path, b'').decode('utf-8-sig').splitlines(True),
                       fromfile='Dvizhok-2/' + path, tofile='Dvizhok-dev-final/' + path))
    (OUT / 'source-diff.patch').write_text(''.join(patches))
    save(OUT / 'summary.json', summary)
    print('DIFF', json.dumps(diff, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
