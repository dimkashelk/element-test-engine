"""Versioned design contracts, deliberately separate from assignment YAML types.

The registry describes infrastructure, not student answers or execution evidence.
Schemas and the bounded validator share one definition (no optional dependency).
"""
import copy
import re

from .indexer import IDENT, split_parameters
from .yaml_io import InvalidTestError


# id: family, owning stage, readiness, implementation, dependencies, first gap
CAPABILITIES = {
    'method-runtime': ('method', 46, 'scoped', 'element_test.execution_plan', [], None),
    'query-source': ('query', 47, 'scoped', 'element_test.storage_queries', ['method-runtime'], None),
    'query-entry': ('query', 47, 'scoped', 'element_test.source_query', ['query-source'], None),
    'clock': ('time-event', 48, 'scoped', 'element_test.session_contracts', ['method-runtime'], None),
    'timer-events': ('time-event', 48, 'scoped', 'element_test.timer_events', ['clock'], 'Only own declaration-resolved ()->ничто callbacks; dynamic/foreign callbacks remain unsupported'),
    'ui-state': ('interface', 49, 'scoped', 'element_test.interface_runtime', ['method-runtime'], 'Nested custom components/native surfaces are declaration-only'),
    'ui-binding': ('interface', 49, 'scoped', 'element_test.interface_runtime', ['ui-state'], 'Computed expressions are read-only; cyclic or unsupported AST remains unavailable'),
    'ui-action': ('interface', 49, 'scoped', 'element_test.interface_runtime', ['ui-state'], 'Metadata lifecycle is opt-in storage.lifecycle; model navigation has a separate runtime.navigation entry'),
    'dynamic-ui': ('interface', 49, 'scoped', 'element_test.interface_runtime', ['ui-state'], 'Only Надпись/АбсолютныйШрифт and insertion into named group contents'),
    'application-ui': ('interface', 49, 'scoped', 'element_test.interface_runtime', ['ui-state'], 'Nested UI/native rendering remains declaration-only; section routes use runtime.navigation'),
    'metadata-structure': ('metadata-lifecycle', 50, 'scoped', 'element_test.model', [], None),
    'metadata-validation': ('metadata-lifecycle', 50, 'scoped', 'element_test.metadata_lifecycle', ['metadata-structure'], 'Declared constraints and tagged nominal reference unions; dynamic numbering prefixes and arbitrary object unions remain unsupported'),
    'storage-lifecycle': ('metadata-lifecycle', 50, 'scoped', 'element_test.storage', ['method-runtime', 'query-source'], 'Atomic object/register/constant staging and partial independent-register filters; empty filters, register handlers and concurrent native lifecycle remain unsupported'),
    'report-layout': ('report', 51, 'scoped', 'element_test.report_runtime', ['query-source', 'metadata-structure'], 'Typed row formulas, roles and stable sorting; native pivot/aggregation UI remains external'),
    'navigation': ('navigation-panel', 51, 'scoped', 'element_test.navigation', ['ui-action'], 'Declaration-resolved model opening; custom form handlers and native rendering remain external'),
    'report-panel': ('navigation-panel', 51, 'scoped', 'element_test.report_runtime', ['report-layout', 'navigation', 'timer-events'], 'Shared storage and fixed-rate full refresh; native dashboard rendering remains external'),
    'branch-measurement': ('branches-invariants', 52, 'scoped', 'element_test.branches', ['method-runtime'], 'Source condition outcomes and loop/selection boundaries; no inference of unreachable paths or native platform acceptance'),
    'generated-invariants': ('branches-invariants', 52, 'scoped', 'element_test.generative', ['branch-measurement'], 'Bounded scalar/array domains, independent SBSL relations and local shrinking; memory method entry, no arbitrary native generators'),
}

FAMILIES = {
    'method': ('reuse', ['runtime'], ['args', 'context', 'storage'], ['call', 'sequence'], ['return', 'state', 'effects'], 'SBSL independent comparison; retain existing runtime contract'),
    'query': ('extend', ['runtime'], ['source selector', 'typed parameters', 'storage'], ['execute', 'repeat after write'], ['typed rows', 'NULL', 'cardinality', 'resources'], 'Extend runtime with original inline/XBQL selector; no new YAML type yet'),
    'time-event': ('extend', ['runtime'], ['clock', 'timezone', 'locale'], ['register', 'cancel', 'advance', 'close'], ['active callbacks', 'clock', 'state'], 'Extend session clock with deterministic queue; original callbacks execute in Script'),
    'interface': ('extend', ['runtime', 'formRequirements'], ['role/type/binding selector', 'typed input', 'context'], ['input', 'recalculate', 'handler', 'open', 'write'], ['structure', 'bindings', 'state', 'saved effects'], 'Extend form context/actions; structure and behavior observed separately'),
    'metadata-lifecycle': ('extend', ['element', 'field', 'method', 'runtime'], ['metadata selector', 'new/existing state', 'write parameters'], ['validate', 'write', 'reload', 'rollback'], ['constraint declaration', 'rejection', 'fresh state', 'register audit'], 'Reuse structural checks/storage; enforce constraints and lifecycle explicitly'),
    'report': ('extend', ['element', 'runtime'], ['report selector', 'typed parameters', 'storage'], ['query', 'evaluate layout'], ['data', 'formula values', 'roles', 'hierarchy', 'sorting'], 'runtime.report selects metadata/XBQL sources; separate report formula IR executes in Script'),
    'navigation-panel': ('extend', ['element', 'runtime'], ['fragment/panel selector', 'visibility context'], ['resolve', 'open', 'refresh'], ['routes', 'availability', 'widgets', 'positions', 'refresh'], 'runtime.navigation and runtime.panel model routes/opening and timer refresh; native rendering outside contract'),
    'branches-invariants': ('extend', ['runtime'], ['seed', 'typed domains', 'independent relation'], ['instrument', 'generate', 'shrink'], ['branch outcomes', 'counterexample', 'seed'], 'Technical coverage has no business points; oracle must be independent'),
}

REASONS = ['CAPABILITY_EXTENSION_REQUIRED', 'SOURCE_PARSE_BLOCKED', 'QUERY_PLAN_BLOCKED',
           'METHOD_PLAN_BLOCKED', 'AUTHOR_CONTEXT_REQUIRED', 'AUTHOR_SCENARIO_ABSENT',
           'STANDALONE_QUERY_ENTRY_REQUIRED', 'ADAPTER_NOT_CHECKED', 'ACCEPTANCE_NOT_CHECKED',
           'REFERENCE_UNRESOLVED', 'EXTERNAL_NATIVE_UI', 'VERSION_APPLICABILITY_NOT_CHECKED']


def registry():
    return {'schemaVersion': 1, 'publicYamlTypesAdded': [],
            'families': [{'id': key, 'apiDecision': v[0], 'existingContracts': v[1],
                          'inputs': v[2], 'actions': v[3], 'observations': v[4],
                          'comparison': 'independent author oracle in SBSL', 'decision': v[5],
                          'capabilities': [k for k, c in CAPABILITIES.items() if c[0] == key]}
                         for key, v in FAMILIES.items()],
            'capabilities': [{'id': key, 'family': v[0], 'stage': v[1], 'readiness': v[2],
                              'implementation': v[3], 'dependencies': v[4], 'firstGap': v[5],
                              'technicalAcceptance': 'not-checked-for-this-source'}
                             for key, v in CAPABILITIES.items()], 'reasonCodes': REASONS}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}


STRING = {'type': 'string', 'minLength': 1}
STRINGS = {'type': 'array', 'items': STRING, 'uniqueItems': True}
HASH = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
POSITION = obj({'range': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}, 'minItems': 2, 'maxItems': 2},
                'line': {'type': 'integer', 'minimum': 1}, 'column': {'type': 'integer', 'minimum': 1},
                'yamlPath': {'type': 'array', 'items': {'type': 'string'}}})
OWNER = obj({'namespace': {'type': 'string'}, 'name': STRING, 'kind': STRING})
SOURCE = obj({'file': STRING, 'fileHash': HASH, 'projectSourceHash': HASH, 'owner': OWNER, 'position': POSITION})
PROFILE = obj({'sourceCompatibilityVersion': STRING, 'adapterProfile': STRING,
               'queryReferenceVersion': STRING, 'versionApplicability': {'enum': ['not-checked', 'verified']}})
FACT = obj({'status': {'enum': ['ready', 'blocked', 'not-checked', 'not-applicable']},
            'reasonCode': {'type': ['string', 'null'], 'enum': REASONS + [None]},
            'basis': STRING, 'detail': {'type': ['string', 'null']}})
STAGES = obj({k: FACT for k in ('discovery', 'parsing', 'planning', 'adapter', 'technicalAcceptance')})


def contract_schema():
    typed = obj({'name': STRING, 'type': STRING})
    action = obj({'operation': STRING, 'inputs': STRINGS})
    observation = obj({'name': STRING, 'type': STRING, 'technicalOnly': {'type': 'boolean'}})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **obj({
        'schemaVersion': {'const': 1}, 'family': {'enum': list(FAMILIES)}, 'selector': SOURCE,
        'profile': PROFILE, 'inputs': {'type': 'array', 'items': typed},
        'actions': {'type': 'array', 'items': action}, 'observations': {'type': 'array', 'items': observation},
        'comparison': obj({'kind': {'enum': ['independent']}, 'oracle': STRING, 'engine': {'enum': ['SBSL']}}),
        'dependencies': {'type': 'array', 'items': {'enum': list(CAPABILITIES)}, 'uniqueItems': True},
        'unavailableReasonCodes': {'type': 'array', 'items': {'enum': REASONS}, 'uniqueItems': True},
        'isolation': obj({'scope': {'enum': ['check-session']}, 'clock': {'enum': ['explicit']},
                          'storage': {'enum': ['fresh']}, 'callbacksOnClose': {'enum': ['cancel']}}),
        'evidence': {'type': 'array', 'items': obj({'sourceHash': HASH, 'origin': STRING, 'stage': STRING})},
    })}


def map_schema():
    item = obj({'id': STRING, 'kind': STRING, 'family': {'enum': list(FAMILIES)}, 'source': SOURCE,
                'profile': PROFILE, 'value': {}, 'requires': {'type': 'array', 'items': {'enum': list(CAPABILITIES)}, 'uniqueItems': True},
                'dependsOn': STRINGS, 'stages': STAGES,
                'authorScenario': obj({'status': {'enum': ['absent', 'declared']}, 'basis': STRING}),
                'firstBlocker': {'type': ['object', 'null'], 'properties': FACT['properties'],
                                 'required': FACT['required'], 'additionalProperties': False}})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **obj({
        'schemaVersion': {'const': 1}, 'sourceHash': HASH, 'projectIdentity': {'type': 'object'},
        'profile': PROFILE, 'archive': STRING, 'archiveSha256': {'type': ['string', 'null']},
        'counts': {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0}},
        'items': {'type': 'array', 'items': item}, 'capabilities': {'type': 'array', 'items': {'type': 'object'}},
        'queue': {'type': 'array', 'items': obj({'stage': {'type': 'integer'}, 'dependencies': {'type': 'array', 'items': {'type': 'integer'}, 'uniqueItems': True},
                    'capabilities': STRINGS, 'occurrences': {'type': 'integer', 'minimum': 0}, 'firstBlockers': STRINGS, 'externalBoundaries': STRINGS})},
        'notes': STRINGS,
    })}


def validate_schema(value, schema, path='$'):
    """Validate the bounded keyword set used by our v1 schemas, not arbitrary JSON Schema."""
    kinds = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool, 'null': type(None)}
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    if types and not any(type(value) is kinds[t] for t in types):
        raise InvalidTestError(path + ': invalid type')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise InvalidTestError(path + ': invalid version/value')
    if 'enum' in schema and not any(type(value) is type(v) and value == v for v in schema['enum']):
        raise InvalidTestError(path + ': unknown value')
    if isinstance(value, dict):
        if set(schema.get('required', [])) - value.keys():
            raise InvalidTestError(path + ': missing fields')
        props = schema.get('properties', {})
        for key, child in value.items():
            extra = schema.get('additionalProperties', {})
            if key not in props and extra is False:
                raise InvalidTestError(path + ': unknown field ' + key)
            validate_schema(child, props.get(key, extra if isinstance(extra, dict) else {}), path + '.' + key)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise InvalidTestError(path + ': invalid length')
        if schema.get('uniqueItems') and any(v in value[:i] for i, v in enumerate(value)):
            raise InvalidTestError(path + ': duplicate items')
        for i, child in enumerate(value):
            validate_schema(child, schema.get('items', {}), path + '[' + str(i) + ']')
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and not re.search(schema['pattern'], value)):
            raise InvalidTestError(path + ': invalid string')
    elif type(value) is int and value < schema.get('minimum', float('-inf')):
        raise InvalidTestError(path + ': out of range')


def validate_source(source):
    a, b = source['position']['range']
    if b < a or source['file'].startswith('/') or '..' in source['file'].split('/') or '\\' in source['file']:
        raise InvalidTestError('Invalid source position/path')


def validate_type(text):
    """Syntactic XBSL types, including nested generics, owner members and unions."""
    import re
    depth, start, parts = 0, 0, []
    for i, ch in enumerate(text):
        depth += (ch == '<') - (ch == '>')
        if depth < 0:
            raise InvalidTestError('Unbalanced input type')
        if ch == '|' and depth == 0:
            parts.append(text[start:i]); start = i + 1
    if depth:
        raise InvalidTestError('Unbalanced input type')
    parts.append(text[start:])
    if len(parts) > 1:
        members = parts[:-1] if parts[-1] == '?' else parts
        if len(members) < 2 or len(set(members)) != len(members):
            raise InvalidTestError('Invalid union type')
        for member in members:
            validate_type(member)
        return
    text = text[:-1] if text.endswith('?') else text
    generic = text.find('<')
    name = text if generic < 0 else text[:generic]
    if not re.fullmatch(IDENT + r'(?:(?:::|\.)' + IDENT + r')*', name):
        raise InvalidTestError('Invalid input type')
    if generic >= 0:
        if not text.endswith('>') or not text[generic + 1:-1].strip():
            raise InvalidTestError('Invalid generic type')
        for argument in split_parameters(text[generic + 1:-1]):
            validate_type(argument.strip())


def validate_contract(value):
    validate_schema(value, contract_schema())
    validate_source(value['selector'])
    for group in ('inputs', 'observations'):
        names = [item['name'] for item in value[group]]
        if len(set(names)) != len(names):
            raise InvalidTestError('Duplicate ' + group)
        for item in value[group]:
            validate_type(item['type'])
    names = {item['name'] for item in value['inputs']}
    if any(set(action['inputs']) - names for action in value['actions']):
        raise InvalidTestError('Action references an undeclared input')
    if any(action['operation'] not in FAMILIES[value['family']][3] for action in value['actions']):
        raise InvalidTestError('Action is outside the family contract')
    if value['family'] == 'branches-invariants' and any(not o['technicalOnly'] for o in value['observations']):
        raise InvalidTestError('Coverage/invariant instrumentation must remain a technical observation')
    if any(e['sourceHash'] != value['selector']['projectSourceHash'] for e in value['evidence']):
        raise InvalidTestError('Evidence belongs to a different source')
    return copy.deepcopy(value)


def validate_map(value):
    validate_schema(value, map_schema())
    ids = [i['id'] for i in value['items']]
    if len(set(ids)) != len(ids):
        raise InvalidTestError('Duplicate occurrence identities')
    for item in value['items']:
        validate_source(item['source'])
        if item['source']['projectSourceHash'] != value['sourceHash'] or item['profile'] != value['profile']:
            raise InvalidTestError('Mixed source/profile')
        if set(item['dependsOn']) - set(ids):
            raise InvalidTestError('Dangling occurrence dependency')
        for stage, fact in item['stages'].items():
            if fact['status'] == 'ready' and fact['reasonCode'] is not None:
                raise InvalidTestError('Ready stage cannot have an unavailable reason')
        blocker = item['firstBlocker']
        if blocker and (blocker['status'] != 'blocked' or blocker['reasonCode'] is None):
            raise InvalidTestError('Invalid first blocker')
    return value
