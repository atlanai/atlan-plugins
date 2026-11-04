# Local structural validator reused from the existing BI migration plugin.
# Rechecked against atlanai/app-metric at 7d5bda651b7404de3aedf69d6907e46a9e4783b6.
# No network operations; structural validity is not execution support.
"""Native Registry draft-4 definitions and one-shot, verified draft imports.

The local validator mirrors typed admission; it does not interpret or execute SQL.
Network writes require the engine's reviewed plan. This module never releases artifacts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from urllib.parse import urlencode


SPEC_VERSION = "1.0-draft.4"
PROFILE = "metric-1.0-draft.4-structural-1"
MAX_DEFINITION_BYTES = 1_048_576
RETIRED_FIELDS = {"status", "owner", "approved_by", "approved_at", "domain"}
HANDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")


class RegistryError(ValueError):
    """Safe diagnostic; messages never include server bodies or submitted values."""
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _object(properties, required=(), *, extra=False):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": extra}


STRING = {"type": "string"}
OPTIONAL_STRING = {"type": ["string", "null"]}
STRINGS = {"type": "array", "items": STRING}
JSON_MAP = {"type": "object", "additionalProperties": {}}
PARAMETER = _object({"name": STRING, "datatype": STRING, "default": {}},
                    ("name", "datatype", "default"))
JOIN = _object({key: STRING for key in ("name", "to", "sql", "cardinality", "origin")},
               ("name", "to", "sql", "cardinality", "origin"))
DIMENSION = _object({
    "name": STRING, "kind": STRING, "sql": STRING, "datatype": STRING,
    "filter_form": OPTIONAL_STRING, "values": {"type": "array", "items": {}},
    "values_truncated": {}, "value_map": {}, "required_join": OPTIONAL_STRING,
    "description": OPTIONAL_STRING,
}, ("name", "kind", "sql", "datatype"))
RESULT = _object({
    "columns": STRINGS, "row_count": {"type": "integer", "minimum": 0, "maximum": 2**64-1},
    "hash": STRING, "executed_at": STRING,
    "rows": {"type": "array", "items": {"type": "array", "items": OPTIONAL_STRING}},
}, ("columns", "row_count", "hash", "executed_at", "rows"))
EXAMPLE = _object({
    "source": STRING, "question": OPTIONAL_STRING, "group_by": STRINGS,
    "filters": JSON_MAP, "parameters": JSON_MAP, "sql": STRING,
    "result": {**RESULT, "type": ["object", "null"]},
}, ("source", "sql"))
PROVENANCE = _object({
    "source": STRING, "execution_count": {"type": ["integer", "null"], "minimum": 0, "maximum": 2**64-1},
    "distinct_analysts": {"type": ["integer", "null"], "minimum": 0, "maximum": 2**64-1},
    "trust_score": {"type": ["number", "null"]}, "observed_in": STRINGS,
    "observed_sql": OPTIONAL_STRING, "proposed_by": STRING, "derived_from": STRINGS,
}, ("source", "proposed_by"))
DEFINITION_SCHEMA = _object({
    "spec_version": {"type": "string", "const": SPEC_VERSION},
    "version": {"type": "integer", "minimum": 1, "maximum": 2**63-1},
    "replaced_by": OPTIONAL_STRING, "dialect": STRING, "formula": STRING,
    "aggregation": _object({"type": STRING, "reaggregation": STRING,
        "null_handling": STRING, "zero_denominator": {}}, ("type",)),
    "output": _object({"alias": STRING, "datatype": STRING, "unit": OPTIONAL_STRING},
                      ("alias", "datatype")),
    "population": OPTIONAL_STRING, "exclusions": STRINGS, "grain": STRING,
    "filters": STRINGS, "mandatory_filters": STRINGS, "depends_on": STRINGS,
    "source": _object({"entity": STRING, "table": OPTIONAL_STRING, "sql": OPTIONAL_STRING,
        "alias": STRING, "timeframe_column": OPTIONAL_STRING,
        "parameters": {"type": "array", "items": PARAMETER}}, ("entity", "alias")),
    "joins": {"type": "array", "items": JOIN},
    "dimensions": {"type": "array", "items": DIMENSION},
    "examples": {"type": "array", "items": EXAMPLE},
    "provenance": {**PROVENANCE, "type": ["object", "null"]},
    "extensions": JSON_MAP,
}, ("spec_version", "version", "dialect", "formula", "aggregation", "output", "source"))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _json_limits(value, *, allow_tuples=False):
    stack, nodes, total = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 16384 or depth > 32:
            return "Definition exceeds the node or nesting limit."
        if isinstance(item, str):
            try:
                size = len(item.encode("utf-8"))
            except UnicodeError:
                return "Definition contains an invalid Unicode string."
            total += size
            if "\x00" in item or size > 262144 or total > 4194304:
                return "Definition contains NUL or exceeds the string limit."
        elif isinstance(item, (dict, list)) or allow_tuples and isinstance(item, tuple):
            if len(item) > 1024:
                return "Definition exceeds a collection limit."
            if isinstance(item, dict):
                if any(not isinstance(k, str) for k in item):
                    return "JSON object keys must be strings."
                stack.extend((k, depth + 1) for k in item)
                stack.extend((v, depth + 1) for v in item.values())
            else:
                stack.extend((v, depth + 1) for v in item)
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int:
            if not -(2**63) <= item <= 2**64-1:
                return "Integer is outside the Registry JSON transport range; use a string."
        elif type(item) is float:
            if not math.isfinite(item):
                return "JSON numbers must be finite."
        else:
            return "Definition contains a non-JSON value."
    try:
        if len(_canonical(value)) > MAX_DEFINITION_BYTES:
            return "Definition exceeds the serialized size limit."
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return "Definition is not bounded JSON."
    return None


def _has_type(value, kind):
    return {"object": lambda: type(value) is dict, "array": lambda: type(value) is list,
            "string": lambda: isinstance(value, str), "null": lambda: value is None,
            "integer": lambda: type(value) is int,
            "number": lambda: type(value) in (int, float),
            "boolean": lambda: type(value) is bool}[kind]()


def _shape(value, schema, path, errors):
    if len(errors) >= 30:
        return
    types = schema.get("type")
    types = [types] if isinstance(types, str) else types
    if types and not any(_has_type(value, t) for t in types):
        errors.append({"rule": "type", "path": path, "message": "Value has the wrong JSON type."})
        return
    if "const" in schema and value != schema["const"]:
        errors.append({"rule": "unsupported_spec", "path": path,
                       "message": "Only the explicit 1.0-draft.4 contract is accepted."})
    if value is None:
        return
    if type(value) in (int, float):
        if ("minimum" in schema and value < schema["minimum"] or
                "maximum" in schema and value > schema["maximum"]):
            errors.append({"rule": "range", "path": path, "message": "Number is outside the supported range."})
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            errors.append({"rule": "deny_unknown_fields", "path": path,
                           "message": "Unknown or retired fields are refused; experimental data belongs in extensions."})
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"rule": "required", "path": path + "." + key,
                               "message": "Required field is missing."})
        for key in props:
            if key in value:
                _shape(value[key], props[key], path + "." + key, errors)
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _shape(item, schema["items"], f"{path}[{index}]", errors)


def validate_definition(definition: dict) -> dict:
    """Mirror the strict DTO and structural rules; vocabulary remains advisory."""
    errors, warnings = [], []
    limit_error = _json_limits(definition)
    if limit_error:
        errors.append({"rule": "resource_limit", "path": "definition", "message": limit_error})
    else:
        _shape(definition, DEFINITION_SCHEMA, "definition", errors)
    if not errors:
        core = [(definition, "dialect"), (definition, "formula"),
                (definition["aggregation"], "type"), (definition["output"], "alias"),
                (definition["output"], "datatype"), (definition["source"], "entity"),
                (definition["source"], "alias")]
        for obj, key in core:
            if not obj[key].strip():
                errors.append({"rule": "required", "path": "definition",
                               "message": "Core strings must be nonempty."})
        src = definition["source"]
        sources = [src.get("table"), src.get("sql")]
        if sum(value is not None for value in sources) != 1 or not any(
                isinstance(value, str) and value.strip() for value in sources):
            errors.append({"rule": "source_shape", "path": "definition.source",
                           "message": "Provide exactly one nonempty table or SQL source."})
        declarations = {}
        for key, rows in (("source.parameters", src.get("parameters", [])),
                          ("joins", definition.get("joins", [])),
                          ("dimensions", definition.get("dimensions", []))):
            names = [r["name"] for r in rows]
            declarations[key] = set(names)
            if any(not n.strip() for n in names) or len(set(names)) != len(names):
                errors.append({"rule": "duplicate_name", "path": "definition." + key,
                               "message": "Declaration names must be nonempty and unique."})
        for dim in definition.get("dimensions", []):
            if dim.get("required_join") is not None and dim["required_join"] not in declarations["joins"]:
                errors.append({"rule": "reference", "path": "definition.dimensions.required_join",
                               "message": "Referenced join is not declared."})
        for example in definition.get("examples", []):
            if any(n not in declarations["dimensions"] for n in example.get("group_by", [])):
                errors.append({"rule": "reference", "path": "definition.examples.group_by",
                               "message": "Referenced dimension is not declared."})
            result = example.get("result")
            if result and any(len(row) != len(result["columns"]) for row in result["rows"]):
                errors.append({"rule": "result_shape", "path": "definition.examples.result.rows",
                               "message": "Result row width must match its columns."})
        def warn(rule, path, message):
            warnings.append({"rule": rule, "path": path, "message": message})
        warn("pilot_spec", "definition.spec_version", "Structural admission does not establish business correctness or SQL validity.")
        for obj, key, values, prefix in (
            (definition["aggregation"], "type", {"sum", "count", "count_distinct", "min", "max", "ratio", "custom"}, "aggregation"),
            (definition["aggregation"], "reaggregation", {"", "sum", "min", "max", "none"}, "aggregation"),
            (definition["aggregation"], "null_handling", {"", "ignore", "zero_fill", "propagate"}, "aggregation"),
            (definition["output"], "datatype", {"integer", "decimal", "string", "boolean", "date", "timestamp"}, "output"),
        ):
            if obj.get(key, "") not in values:
                warn("draft_vocabulary", f"definition.{prefix}.{key}", "Value is outside the draft vocabulary; meaning is not interpreted.")
        if definition["aggregation"]["type"] == "ratio" and definition["aggregation"].get("zero_denominator") is None:
            warn("ratio_policy", "definition.aggregation.zero_denominator", "Ratio has no zero-denominator policy recorded.")
        if not definition.get("examples"):
            warn("examples_missing", "definition.examples", "No usage examples are recorded.")
        if definition.get("extensions"):
            warn("experimental_fields", "definition.extensions", "Experimental fields are preserved without validating their meaning.")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "profile": PROFILE, "sql_validation": "not_performed"}

