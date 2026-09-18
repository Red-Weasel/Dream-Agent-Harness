"""Tool arguments fail early without revealing supplied values or fetching refs."""
import socket

import pytest

from dream.core.tool_validation import validate_arguments


@pytest.mark.parametrize('schema,args', [
    ({'type':'object','properties':{'count':{'type':'integer','minimum':0}},'required':['count']}, {'count':0}),
    ({'type':'object','properties':{'enabled':{'type':'boolean'}}}, {'enabled':False}),
    ({'type':'object','properties':{'value':{'type':['string','null']}}}, {'value':None}),
    ({'type':'object','properties':{'value':{'type':'string'}}}, {'value':''}),
    ({'type':'object','properties':{'mode':{'enum':['quick','balanced']}}}, {'mode':'quick'}),
    ({'$defs':{'count':{'type':'integer'}},'type':'object','properties':{'n':{'$ref':'#/$defs/count'}}}, {'n':2}),
    ({'type':'object','properties':{'x':{'enum':[0]}}}, {'x':0}),
])
def test_valid_falsey_and_local_refs(schema, args):
    assert validate_arguments(schema,args) is None


@pytest.mark.parametrize('schema,args,fragment', [
    ({'type':'object','required':['path']}, {}, 'path'),
    ({'type':'object','properties':{'options':{'type':'object','required':['mode']}}}, {'options':{}}, 'options'),
    ({'type':'object','properties':{'n':{'type':'integer'}}}, {'n':True}, 'integer'),
    ({'type':'object','properties':{'n':{'type':'integer','minimum':1}}}, {'n':0}, 'minimum'),
    ({'type':'object','properties':{'n':{'type':'number','exclusiveMaximum':3}}}, {'n':3}, 'exclusiveMaximum'),
    ({'type':'object','properties':{'mode':{'enum':['quick','balanced']}}}, {'mode':'secret-value'}, 'enum'),
    ({'type':'object','properties':{'x':{'enum':[0]}}}, {'x':False}, 'enum'),
    ({'type':'object','additionalProperties':False}, {'unexpected':'secret-value'}, 'additionalProperties'),
    ({'type':'object','properties':{'rows':{'type':'array','items':{'type':'integer'}}}}, {'rows':[1,'secret-value']}, 'rows'),
    ({'type':'object','properties':{'code':{'type':'string','pattern':'^[A-Z]+$'}}}, {'code':'secret-value'}, 'pattern'),
])
def test_invalid_values_are_concise_and_private(schema, args, fragment):
    error = validate_arguments(schema,args)
    assert error and fragment in error
    assert 'secret-value' not in error
    assert len(error) <= 300


def test_untrusted_refs_never_fetch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network must never be used')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    error = validate_arguments({'type':'object','properties':{'x':{'$ref':'https://example.test/schema'}}}, {'x':42})
    assert error and 'schema' in error.lower()
    assert '42' not in error


@pytest.mark.parametrize('schema', [
    {'type':'no-such-type'}, {'required':'path'}, {'$schema':'https://example.test/unknown'},
])
def test_invalid_schema_is_explicit(schema):
    error = validate_arguments(schema,{})
    assert error and 'schema' in error.lower()


def test_repeated_compile_cache_and_changed_schema():
    schema = {'type':'object','properties':{'count':{'type':'integer'}}}
    assert validate_arguments(schema,{'count':1}) is None
    schema['properties']['count']['minimum']=2
    assert validate_arguments(schema,{'count':1}) is not None
    assert validate_arguments(schema,{'count':2}) is None


def test_huge_user_value_and_deep_field_names_do_not_expand_error():
    schema={'type':'object','properties':{'x':{'type':'integer'}}}
    error = validate_arguments(schema, {'x':'private-token-'*1000})
    assert error and len(error)<=300 and 'private-token' not in error


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), object()])
def test_non_json_values_fail_without_echo(value):
    error = validate_arguments({'type':'object'}, {'value':value})
    assert error and 'finite JSON' in error and len(error)<=300


def test_cache_reuses_equivalent_schema_and_is_bounded():
    from dream.core.tool_validation import _compiled
    _compiled.cache_clear()
    schema = {'type':'object','properties':{'n':{'type':'integer'}}}
    assert validate_arguments(schema,{'n':0}) is None
    assert validate_arguments({'properties':schema['properties'],'type':'object'}, {'n':1}) is None
    info = _compiled.cache_info()
    assert info.misses == 1 and info.hits == 1
    for limit in range(130):
        validate_arguments({'type':'integer','minimum':limit}, 150)
    assert _compiled.cache_info().currsize == 128


def test_boolean_schemas_and_known_older_dialect():
    assert validate_arguments(True, {}) is None
    assert validate_arguments(False, {}) is not None
    schema={'$schema':'http://json-schema.org/draft-07/schema#','type':'integer'}
    assert validate_arguments(schema, 1) is None
    assert validate_arguments(schema, True) is not None


@pytest.mark.parametrize('map_schema', [
    {'additionalProperties': {'type': 'integer'}},
    {'patternProperties': {'.*': {'type': 'integer'}}},
])
def test_dynamic_dictionary_keys_are_not_echoed_in_validation_errors(map_schema):
    schema = {'type': 'object', 'properties': {'counts': {'type': 'object', **map_schema}}}
    message = validate_arguments(schema, {'counts': {'private-client@example.test': 'wrong'}})
    assert message and 'integer' in message and 'counts' in message
    assert 'private-client' not in message and 'example.test' not in message


def test_static_nested_field_guidance_survives_dynamic_key_redaction():
    schema = {'type': 'object', 'properties': {'records': {
        'type': 'object', 'additionalProperties': {
            'type': 'object', 'properties': {'count': {'type': 'integer'}},
        },
    }}}
    message = validate_arguments(schema, {'records': {'private-key': {'count': 'wrong'}}})
    assert message and 'records' in message and 'count' in message
    assert 'private-key' not in message
