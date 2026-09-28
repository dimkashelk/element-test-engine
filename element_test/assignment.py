"""Convert teacher YAML into the neutral contract consumed by SBSL."""
from pathlib import Path
from decimal import Decimal, InvalidOperation

from .types import parse_type
from .yaml_io import InputError, load_yaml, normalize

ALIASES = {"scale": "ДлинаДробнойЧасти", "precision": "ДлинаЦелойЧасти", "length": "МаксимальнаяДлина",
           "visibility": "ОбластьВидимости", "default": "ЗначениеПоУмолчанию", "min": "МинимальноеЗначение",
           "max": "МаксимальноеЗначение", "hierarchical": "Иерархический", "unique": "Уникальность",
           "periodicity": "Периодичность"}


def load_assignment(source):
    source = Path(source)
    main = source / "assignment.yaml" if source.is_dir() else source
    config = load_yaml(main)
    checks = list(config.get("checks", []))
    if source.is_dir():
        for directory in ("structural", "runtime"):
            for path in sorted((source / directory).glob("*.yaml")):
                data = load_yaml(path)
                checks.extend(data.get("checks", [data] if "id" in data else []))
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not isinstance(check.get("type"), str):
            raise InputError("Каждая проверка должна иметь строковые id и type")
        if check["id"] in seen:
            raise InputError(f"Повторяющийся id проверки: {check['id']}")
        seen.add(check["id"])
        if "group" in check and (not isinstance(check["group"], str) or not check["group"].strip()):
            raise InputError(f"{check['id']}: group должен быть непустой строкой")
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
    return {"name": config.get("name", main.parent.name), "checks": checks}
