"""Exact JSON values shared by generated domains and runtime literals."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import math
import re

from .yaml_io import InvalidTestError

TEMPORAL = {'Дата', 'ДатаВремя', 'Момент'}


def number(value):
    if isinstance(value, dict) and set(value) == {'decimal'}:
        value = value['decimal']
        if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value):
            raise InvalidTestError('decimal требует конечную десятичную строку')
    elif type(value) not in {int, float} or type(value) is float and not math.isfinite(value):
        raise InvalidTestError('Требуется конечное Число')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise InvalidTestError('Некорректное Число') from exc
    if not result.is_finite() or result.copy_abs() > 1000000 or result.as_tuple().exponent < -24:
        raise InvalidTestError('Число превышает границы ±1000000 / 24 знака дроби')
    return result


def packed(value):
    return int(value) if value == value.to_integral_value() else {'decimal': format(value, 'f')}


def temporal(typ, value):
    pattern = {'Дата': r'\d{4}-\d{2}-\d{2}',
               'ДатаВремя': r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}',
               'Момент': r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z'}[typ]
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise InvalidTestError('Некорректная типизированная граница ' + typ)
    try:
        return date.fromisoformat(value) if typ == 'Дата' else datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise InvalidTestError('Некорректная календарная дата ' + typ) from exc


def time_text(typ, value):
    if typ == 'Момент':
        return value.isoformat(timespec='milliseconds' if value.microsecond else 'seconds').replace('+00:00', 'Z')
    return value.isoformat().replace('+00:00', 'Z')


def ticks(domain):
    typ = domain['type'].removesuffix('?')
    if typ == 'Число':
        scale = domain.get('scale', 0)
        with localcontext() as ctx:
            ctx.prec = 80
            factor = Decimal(10) ** scale
            low, high = number(domain['min']) * factor, number(domain['max']) * factor
        if low != low.to_integral_value() or high != high.to_integral_value():
            raise InvalidTestError('Границы Число должны лежать на сетке scale')
        return int(low), int(high)
    low, high = temporal(typ, domain['min']), temporal(typ, domain['max'])
    delta = high - low
    seconds = delta.days * 86400 + delta.seconds
    return 0, delta.days if typ == 'Дата' else seconds


def from_tick(domain, value):
    typ = domain['type'].removesuffix('?')
    if typ == 'Число':
        with localcontext() as ctx:
            ctx.prec = 80
            return packed(Decimal(value).scaleb(-domain.get('scale', 0)))
    low = temporal(typ, domain['min'])
    return time_text(typ, low + (timedelta(days=value) if typ == 'Дата' else timedelta(seconds=value)))


def value_tick(domain, value):
    typ = domain['type'].removesuffix('?')
    if typ == 'Число':
        with localcontext() as ctx:
            ctx.prec = 80
            tick = number(value).scaleb(domain.get('scale', 0))
        if tick != tick.to_integral_value():
            raise InvalidTestError('Значение вне сетки scale')
        return int(tick)
    delta = temporal(typ, value) - temporal(typ, domain['min'])
    if delta.microseconds:
        raise InvalidTestError('Значение вне секундной сетки')
    return delta.days if typ == 'Дата' else delta.days * 86400 + delta.seconds


def json_value(value, depth=0):
    if depth > 6:
        return False
    if value is None or type(value) in {bool, int, float, str}:
        if type(value) in {int, float}:
            try: number(value)
            except InvalidTestError: return False
        return not isinstance(value, str) or len(value) <= 128
    if isinstance(value, list):
        return len(value) <= 32 and all(json_value(v, depth+1) for v in value)
    if isinstance(value, dict):
        if set(value) == {'decimal'}:
            try: number(value)
            except InvalidTestError: return False
            return True
        return len(value) <= 32 and all(isinstance(k, str) and len(k) <= 128 and json_value(v, depth+1) for k,v in value.items())
    return False
