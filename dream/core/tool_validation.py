"""Bounded cached JSON Schema validation before a tool is invoked.

Schemas are normalized by the caller. Errors never include argument values.
External references cannot be retrieved; local schema references remain usable.
"""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Any

from jsonschema import exceptions, validators
from referencing import Registry
from referencing.exceptions import Unresolvable

_MAX_ERROR = 300


@lru_cache(maxsize=128)
def _compiled(serialized: str):
    schema = json.loads(serialized)
    validator_type = (validators.validator_for(schema, default=None)
                      if isinstance(schema, dict) and '$schema' in schema
                      else validators.Draft202012Validator)
    if validator_type is None:
        raise ValueError('Unsupported JSON Schema dialect.')
    validator_type.check_schema(schema)
    # Registry's default retrieve raises NoSuchResource. Never use the legacy
    # resolver, which may fetch a remote $ref while validating model arguments.
    return validator_type(schema, registry=Registry())


def _location(error) -> str:
    # Map keys can be user data, such as an email address or a secret identifier.
    # Reveal only names explicitly declared by properties along this schema path.
    schema_path = list(error.absolute_schema_path)
    fields = {schema_path[index + 1] for index, part in enumerate(schema_path[:-1])
              if part == 'properties'}
    parts = [str(part) if isinstance(part, int) or part in fields else '<field>'
             for part in error.absolute_path]
    return '.'.join(parts)[:100] or 'arguments'


def _describe(error) -> str:
    location, rule = _location(error), error.validator
    if rule == 'required' and isinstance(error.instance, dict):
        missing = [str(key) for key in error.validator_value if key not in error.instance]
        detail = 'missing required field ' + ', '.join(missing)
    elif rule == 'type':
        types = error.validator_value
        detail = 'expected ' + (', '.join(types) if isinstance(types, list) else str(types))
    elif rule == 'additionalProperties':
        detail = 'additionalProperties disallows an unexpected field; use only schema fields'
    elif rule in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
                  'minLength', 'maxLength', 'minItems', 'maxItems', 'minProperties', 'maxProperties'):
        detail = f'{rule} constraint is {error.validator_value}'
    elif rule in ('enum', 'const', 'pattern', 'format', 'uniqueItems', 'anyOf', 'oneOf', 'allOf', 'not'):
        detail = f'does not satisfy {rule}; use the tool schema'
    else:
        detail = f'does not satisfy {rule or "the tool schema"}'
    return f'Invalid tool arguments at {location}: {detail}. Nothing ran.'[:_MAX_ERROR]


def validate_arguments(schema: dict[str, Any] | bool, args: Any) -> str | None:
    """Return None if valid, otherwise an actionable error of at most 300 chars."""
    try:
        serialized = json.dumps(schema, sort_keys=True, separators=(',', ':'), allow_nan=False)
        validator = _compiled(serialized)
    except (exceptions.SchemaError, TypeError, ValueError, RecursionError):
        return 'Tool schema is invalid or uses an unsupported dialect. Nothing ran; repair the tool schema.'
    try:
        # Native JSON arguments cannot include NaN, infinity or Python-only values.
        json.dumps(args, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return 'Invalid tool arguments: expected finite JSON values. Nothing ran.'
    try:
        error = next(validator.iter_errors(args), None)
    except Unresolvable:
        return 'Tool schema reference could not be resolved locally. Nothing ran; provide a self-contained schema.'
    except (exceptions.SchemaError, RecursionError):
        return 'Tool schema could not be evaluated. Nothing ran; repair the tool schema.'
    return _describe(error) if error is not None else None
