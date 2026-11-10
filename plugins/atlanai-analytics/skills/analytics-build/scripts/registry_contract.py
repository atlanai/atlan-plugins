"""Offline typed admission from checked-in Registry definition schemas; no network calls."""
import json
import math
from functools import lru_cache
from pathlib import Path


CONTRACTS = Path(__file__).resolve().parents[2] / 'analytics-metrics/modules/metrics/contracts'
FILES = {'metric': 'metric-definition.schema.json', 'sql_query': 'sql-query-definition.schema.json'}


@lru_cache(maxsize=2)
def schema(kind):
    return json.loads((CONTRACTS / FILES[kind]).read_text())


def _json_error(value):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 16384 or depth > 32:
            return 'definition exceeds the JSON node or nesting limit'
        if type(item) is dict:
            if any(type(k) is not str for k in item):
                return 'definition must have string object keys'
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
        elif type(item) is int and not -(2**63) <= item <= 2**64-1:
            return 'definition contains an integer outside the JSON transport range'
        elif type(item) is float and not math.isfinite(item):
            return 'definition must contain finite JSON numbers'
        elif item is not None and type(item) not in (str, int, float, bool):
            return 'definition must contain JSON values only'
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 1_048_576:
            return 'definition exceeds the JSON size limit'
    except (ValueError, TypeError, UnicodeError):
        return 'definition must contain valid JSON'
    return None


def _type(value, name):
    return {'object': type(value) is dict, 'array': type(value) is list,
            'string': type(value) is str, 'integer': type(value) is int,
            'number': type(value) in (int, float), 'boolean': type(value) is bool,
            'null': value is None}[name]


def _validate(value, rule, path, errors):
    if rule is True or len(errors) >= 30:
        return
    if rule is False:
        errors.append(path + ' is not allowed')
        return
    kinds = rule.get('type', [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    if kinds and not any(_type(value, k) for k in kinds):
        errors.append(path + ' must be ' + ' or '.join(kinds))
        return
    if 'const' in rule and value != rule['const']:
        errors.append(path + ' must match the supported spec version')
    if 'enum' in rule and value not in rule['enum']:
        errors.append(path + ' must use a supported value')
    if type(value) in (int, float):
        if ('minimum' in rule and value < rule['minimum'] or
                'maximum' in rule and value > rule['maximum']):
            errors.append(path + ' is outside the supported range')
    if type(value) is dict:
        props = rule.get('properties', {})
        for key in rule.get('required', []):
            if key not in value:
                errors.append(path + '.' + key + ' is required')
        if rule.get('additionalProperties') is False and set(value) - set(props):
            errors.append(path + ' contains undeclared or retired fields; use extensions for extra data')
        for key, subrule in props.items():
            if key in value:
                _validate(value[key], subrule, path + '.' + key, errors)
    elif type(value) is list and 'items' in rule:
        for index, item in enumerate(value):
            _validate(item, rule['items'], f'{path}[{index}]', errors)


def definition_errors(kind, definition):
    """Validate the shipped schema subset before semantic or SQL inspection touches values."""
    error = _json_error(definition)
    if error:
        return [error]
    errors = []
    _validate(definition, schema(kind), 'definition', errors)
    return errors
