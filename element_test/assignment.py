"""Convert teacher YAML into the neutral contract consumed by SBSL."""
from pathlib import Path
from decimal import Decimal, InvalidOperation

from .types import parse_type
from .yaml_io import InputError, load_yaml, normalize

ALIASES = {"scale": "ДлинаДробнойЧасти", "precision": "ДлинаЦелойЧасти", "length": "МаксимальнаяДлина",
           "visibility": "ОбластьВидимости", "default": "ЗначениеПоУмолчанию", "min": "МинимальноеЗначение",
           "max": "МаксимальноеЗначение", "hierarchical": "Иерархический", "unique": "Уникальность",
           "periodicity": "Периодичность"}


def load_assignment(source, *, max_yaml_bytes=4 * 1024 * 1024):
    source = Path(source)
    main = source / "assignment.yaml" if source.is_dir() else source
    config = load_yaml(main, max_bytes=max_yaml_bytes)
    if 'profile' in config:
        from .profiles import normalize_profile
        if source.is_dir() and any((source/d).exists() for d in ('structural','runtime')):
            raise InputError('Встроенный профиль не принимает дополнительные проверки')
        return normalize_profile(config)
    checks = list(config.get("checks", []))
    if 'formRequirements' in config:
        from .form_requirements import expand_requirements
        contracts = config['formRequirements']
        if not isinstance(contracts, list):
            contracts = [contracts]
        for contract in contracts:
            if isinstance(contract, str):
                contract = load_yaml(main.parent / contract, max_bytes=max_yaml_bytes)
            checks.extend(expand_requirements(contract))
    if source.is_dir():
        for directory in ("structural", "runtime"):
            for path in sorted((source / directory).glob("*.yaml")):
                data = load_yaml(path, max_bytes=max_yaml_bytes)
                checks.extend(data.get("checks", [data] if "id" in data else []))
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not isinstance(check.get("type"), str):
            raise InputError("Каждая проверка должна иметь строковые id и type")
        if check["id"] in seen:
            raise InputError(f"Повторяющийся id проверки: {check['id']}")
        seen.add(check["id"])
        if "failureExplanation" in check and (not isinstance(check["failureExplanation"], str)
                                               or not check["failureExplanation"].strip()):
            raise InputError(f"{check['id']}: failureExplanation должен быть непустой строкой")
        if "group" in check and (not isinstance(check["group"], str) or not check["group"].strip()):
            raise InputError(f"{check['id']}: group должен быть непустой строкой")
        if "library" in check:
            library = check["library"]
            if (not isinstance(library, dict) or set(library) != {"provider", "name", "version"}
                    or any(not isinstance(value, str) or not value.strip() for value in library.values())):
                raise InputError(f"{check['id']}: library требует provider, name и version")
        try:
            points = Decimal(str(check.get("points", check.get("weight", 1))))
            if not points.is_finite() or points < 0:
                raise InvalidOperation
            check["points"] = float(points)
        except (InvalidOperation, ValueError):
            raise InputError(f"{check['id']}: points должен быть неотрицательным числом")
        properties = check.setdefault("properties", {})
        if not isinstance(properties, dict):
            raise InputError(f"{check['id']}: properties должен быть объектом")
        for alias, key in ALIASES.items():
            if alias in check:
                properties[key] = check[alias]
        check["properties"] = normalize(properties)
        if "expectedType" in check:
            try:
                check["expectedType"] = parse_type(check["expectedType"])[0]
            except ValueError as exc:
                raise InputError(str(exc)) from exc
    if not checks:
        raise InputError("Задание не содержит проверок")
    result = {"name": config.get("name", main.parent.name), "checks": checks}
    if 'executionOptions' in config:
        from .assignment_queries import normalize_options
        result['executionOptions'] = normalize_options(config['executionOptions'])
    runtime_ids = {check['id'] for check in checks if check['type'] == 'runtime'}
    for check in checks:
        if check['type'] != 'assertion':
            continue
        from .assignment_queries import validate_assertion
        if 'execution' in check:
            raise InputError('execution формирует движок проверки')
        validate_assertion(check.get('assertion'))
        for clause in check['assertion']['all']:
            query = clause['query']
            if query['from'] == 'execution' and query['criterion'] not in runtime_ids:
                raise InputError('Запрос execution должен ссылаться на runtime-критерий')
    return result
