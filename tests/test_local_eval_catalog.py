"""Descriptor copies and denied executors; synthetic supplied tool catalog only."""
from copy import deepcopy
import importlib
import importlib.util

from claude_agent_sdk import SdkMcpTool
import pytest


def catalog_api():
    name = 'dream.local_eval_catalog'
    assert importlib.util.find_spec(name) is not None, 'The local builtin catalog module is absent.'
    build = getattr(importlib.import_module(name), 'build_tool_catalog', None)
    assert callable(build), 'The bounded build_tool_catalog API is absent.'
    return build


def tool(name, handler, *, description=None, schema=None):
    return SdkMcpTool(name=name, description=description or 'Generic shipped description for ' + name,
        input_schema=schema if schema is not None else {'type': 'object', 'properties': {
            'path': {'type': 'string', 'enum': ['sample.txt']}}}, handler=handler)


async def source_must_not_execute(arguments):
    raise AssertionError('Source handlers must never be used by descriptor clones.')


def test_catalog_api_is_explicit():
    catalog_api()


async def test_only_guarded_allowed_callable_survives_and_denial_does_not_call_source():
    build = catalog_api(); calls = []
    async def guarded(arguments):
        calls.append(deepcopy(arguments))
        return {'content': [{'type': 'text', 'text': 'Actual guarded fixture result.'}]}
    sources = [tool('read_file', source_must_not_execute), tool('web_search', source_must_not_execute)]
    allowed = tool('read_file', guarded, description='Fixture guard description must not replace shipped metadata.')
    clones, metadata = build([allowed], source_tools=sources)
    by_name = {entry.name: entry for entry in clones}
    assert list(by_name) == ['read_file', 'web_search']
    assert metadata['kind'] == 'builtin_tools' and type(metadata['count']) is int
    assert metadata['count'] == 2
    assert set(metadata['allowed']) == {'read_file'} and set(metadata['denied']) == {'web_search'}
    assert by_name['read_file'] is not allowed and by_name['read_file'] is not sources[0]
    assert by_name['read_file'].handler is guarded
    for source in sources:
        clone = by_name[source.name]
        assert clone.description == source.description and clone.input_schema == source.input_schema
        assert clone is not source and clone.input_schema is not source.input_schema
    result = await by_name['read_file'].handler({'path': 'sample.txt'})
    assert result['content'][0]['text'] == 'Actual guarded fixture result.'
    denied = await by_name['web_search'].handler({'query': 'Do not actually search.'})
    assert denied.get('is_error') is True or denied.get('isError') is True
    assert isinstance(denied.get('content'), list) and denied['content']
    assert calls == [{'path': 'sample.txt'}]


def test_nested_schema_mutation_cannot_change_source_allowed_object_or_other_build():
    build = catalog_api()
    source = tool('read_file', source_must_not_execute)
    allowed = tool('read_file', source_must_not_execute)
    original_source = deepcopy(source.input_schema); original_allowed = deepcopy(allowed.input_schema)
    first, _ = build([allowed], source_tools=[source])
    second, _ = build([allowed], source_tools=[source])
    first[0].input_schema['properties']['path']['enum'].append('changed')
    first[0].description = 'changed description'
    assert source.input_schema == original_source and allowed.input_schema == original_allowed
    assert second[0].input_schema == original_source and second[0].description == source.description
    source.input_schema['properties']['path']['enum'].append('later source mutation')
    assert second[0].input_schema == original_source


@pytest.mark.parametrize('names', [('same', 'same'), ('same', 'SAME'), ('read_file', 'Read_File')])
def test_duplicate_or_case_colliding_source_names_refuse(names):
    build = catalog_api()
    with pytest.raises(ValueError):
        build([], source_tools=[tool(name, source_must_not_execute) for name in names])


@pytest.mark.parametrize('name', ['tool_schema', 'tool_lookup', 'tool_call', 'task', 'snip', 'fork_verifier_agent',
                                'TOOL_SCHEMA', 'Tool_Call', 'TASK'])
def test_backend_control_names_cannot_be_catalog_decoys(name):
    build = catalog_api()
    with pytest.raises(ValueError): build([], source_tools=[tool(name, source_must_not_execute)])


def test_unknown_allowed_tool_refuses_instead_of_widening_catalog():
    build = catalog_api()
    with pytest.raises(ValueError):
        build([tool('unknown_extra', source_must_not_execute)],
              source_tools=[tool('read_file', source_must_not_execute)])


def test_catalog_count_ceiling_accepts_256_and_refuses_257():
    build = catalog_api()
    sources = [tool('fixture_' + str(index), source_must_not_execute) for index in range(257)]
    clones, metadata = build([], source_tools=sources[:256])
    assert len(clones) == metadata['count'] == 256
    assert set(metadata['denied']) == {source.name for source in sources[:256]}
    assert not metadata['allowed']
    with pytest.raises(ValueError): build([], source_tools=sources)


@pytest.mark.parametrize('location', ['description', 'schema'])
def test_metadata_byte_ceiling_counts_utf8_including_schema(location):
    build = catalog_api()
    oversized = '界' * 180_000  # 540,000 UTF-8 bytes, despite fewer than512Ki characters.
    source = tool('fixture', source_must_not_execute)
    if location == 'description': source.description = oversized
    else: source.input_schema['properties']['path']['description'] = oversized
    with pytest.raises(ValueError): build([], source_tools=[source])


def test_serialized_metadata_limit_applies_to_aggregate_not_only_individual_tools():
    build = catalog_api()
    sources = [tool('fixture_' + str(index), source_must_not_execute, description='x' * 270_000)
               for index in range(2)]
    assert len(build([], source_tools=sources[:1])[0]) == 1
    with pytest.raises(ValueError): build([], source_tools=sources)
