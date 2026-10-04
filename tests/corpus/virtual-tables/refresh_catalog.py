"""Refresh only the saved task-43 scope; an inventory entry is never a PASS."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_catalog import attach_evidence, markdown
from element_test.query_plan import parse_storage_query
from element_test.yaml_io import InputError
from virtual_fixtures import CORPUS, EVIDENCE, REPO, check


def main():
    baseline = json.loads((REPO / 'docs/query-stage-043-baseline.json').read_text())
    reference = json.loads((CORPUS / 'reference-9.3.json').read_text())
    reference.update(executorVersion='10.0.2-1', executionCompatibilityVersion='current', nativeXBQL=False)
    path = REPO / 'docs/query-contract-catalog.json'
    catalog = json.loads(path.read_text())
    selected = {r['contractId']: r for r in baseline['contracts']}
    records = [r for r in catalog['contracts'] if r['contractId'] in selected]
    assert len(records) == len(selected)
    groups = {}
    for r in records:
        before = selected[r['contractId']]
        for key in ('sourceHash', 'projectSourceHash', 'file', 'range', 'text', 'fixture'):
            assert r.get(key) == before.get(key), (r['contractId'], key)
        r.update(planned=False, executed=False, independentlyAssessed=False,
                 criteria=[], evidence=[], reference043={
                     'inventory': str((CORPUS / 'reference-9.3.json').relative_to(REPO)),
                     'inventoryHash': sha256((CORPUS / 'reference-9.3.json').read_bytes()).hexdigest(),
                     'referenceVersion': '9.3', 'executorVersion': '10.0.2-1',
                     'executionCompatibilityVersion': 'current', 'nativeXBQL': False})
        if r.get('archive'):
            identity = r['projectIdentity']
            groups.setdefault((r['archive'], identity['Поставщик'] + '::' + identity['Имя']), []).append(r)
    for (archive, identity), group in groups.items():
        with open_project(REPO / archive, identity) as root:
            model = analyze(root)
            modules = {m['sourceFile']: m for m in model['modules']}
            for r in group:
                source = (root / r['file']).read_text(encoding='utf-8-sig')
                assert sha256((root / r['file']).read_bytes()).hexdigest() == r['sourceHash']
                assert model['sourceHash'] == r['projectSourceHash']
                assert source[r['bodyStart']:r['range'][1] - (r['family'] == 'literal')] == r['text']
                module = modules.get(r['file'])
                c = ProjectTypes(model, module['namespace'] if module else r['ownerIdentity']['namespace'],
                                 module.get('imports', []) if module else [])
                c.rename_collisions = True
                c.reference_id_type = 'Ууид'
                c.query_root = root
                try:
                    r['ast'] = parse_storage_query(r['text'], c).to_dict()
                    r.update(parsed=True, unsupportedReason=None,
                             semantics=['adapter-storage-virtual-sources-043', 'element-reference-9.3', 'script-current'])
                except InputError as exc:
                    r.update(ast=None, parsed=False, unsupportedReason=str(exc))
                    if r['family'] == 'xbql-file':
                        r['unsupportedReason'] = 'External & parameter bindings need the typed Query API/result contract of stage 44; no source rewrite or fabricated execution criterion'
    attach_evidence(records, [EVIDENCE / ('public-real-' + mode) for mode in ('test', 'run')])
    root = CORPUS / 'documented'
    model = analyze(root)
    for r in records:
        if r['family'] == 'documented-form':
            method = r['contractId'].removeprefix('documented-').replace('-', '_')
            fixture = REPO / r['fixture']
            assert fixture.read_text().strip() == r['text'].strip()
            receipt_path = EVIDENCE / ('assessment-documented-' + method + '.json')
            receipt = json.loads(receipt_path.read_text())
            runtime = json.loads((REPO / receipt['runtime']).read_text())
            assert receipt['status'] == 'PASS' and receipt['engine'] == 'SBSL'
            assert receipt['criterionId'] == method and runtime['status'] == 'EXECUTED'
            assert receipt['sourceHash'] == model['sourceHash']
            cfg = check(method=method)
            cfg['args'] = ['2026-10-01'] if method.startswith('slice') else []
            q = plan_execution(root, model, cfg).queries[0]
            assert q['text'].strip() == r['text'].strip()
            source = root / q['sourceFile']
            assert source.read_text()[q['start']:q['end']] == 'Запрос{' + q['text'] + '}'
            r.update(ast=q['ast'], parsed=True, planned=True, executed=True,
                     independentlyAssessed=True, unsupportedReason=None,
                     fixtureStatus='executable-transferable-project',
                     fixtureHash=sha256(fixture.read_bytes()).hexdigest(), referenceVersion='9.3',
                     semantics=['adapter-storage-virtual-sources-043', 'element-reference-9.3', 'script-current'])
            r['criteria'] = [{'criterionId': method, 'direct': True, 'status': 'PASS',
                              'origin': 'fresh-043-transferable',
                              'sourceHash': sha256(source.read_bytes()).hexdigest(),
                              'projectSourceHash': model['sourceHash'], 'sourceFile': q['sourceFile'],
                              'range': [q['start'], q['end']], 'fixtureHash': r['fixtureHash']}]
            r['evidence'] = [str(receipt_path.relative_to(REPO)), receipt['runtime']]
        elif r['family'] == 'documentation-index':
            ident = r['contractId']
            reason = 'Documentation inventory has no exact executable source/range/criterion; portable family tests do not assess this inventory occurrence'
            if 'entitycontract' in ident:
                reason += '; entity-contract tables were removed after platform 7.0 (reference 9.3)'
            elif any(s in ident for s in ('changes', 'exchangeplan', 'undeletedobjects', 'settingsstorage')):
                reason += '; exchange registration/settings/cleanup lifecycle needs native platform storage and rights, currently unavailable'
            elif 'users' in ident:
                reason += '; adapter covers six explicit identity fields, locale/authentication/rights remain unavailable'
            elif 'virtualtable' in ident:
                reason += '; only no-parameter non-null saved definitions are supported'
            r.update(unsupportedReason=reason, referenceVersion='9.3')
        if r['family'] == 'literal' and r['independentlyAssessed']:
            assert r['parsed'] and r['planned'] and r['executed']
            for criterion in r['criteria']:
                criterion.update(origin='fresh-043-direct', sourceHash=r['sourceHash'],
                                 projectSourceHash=r['projectSourceHash'], sourceFile=r['file'], range=r['range'])
    archives = [{**a, 'unchanged': sha256((REPO / a['file']).read_bytes()).hexdigest() == a['sha256']}
                for a in baseline['archives']]
    assert all(a['unchanged'] for a in archives)
    counts = {k: sum(bool(r.get(k)) for r in records)
              for k in ('parsed', 'planned', 'executed', 'independentlyAssessed')}
    catalog['stage043Reference'] = reference
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    path.with_suffix('.md').write_text(markdown(catalog))
    report = {'stage': '043', 'scopeCount': len(records), 'counts': counts,
              'archives': archives, 'reference': reference, 'contracts': records,
              'remainingCauses': dict(Counter(r['unsupportedReason'] for r in records if r.get('unsupportedReason')))}
    write_json(EVIDENCE / 'catalog-stage-043.json', report)
    write_json(REPO / 'docs/query-stage-043-coverage.json', report)
    print(counts)


if __name__ == '__main__':
    main()
