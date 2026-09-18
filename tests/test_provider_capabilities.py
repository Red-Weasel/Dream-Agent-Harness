import pytest


def test_names_and_settings_are_not_server_capabilities():
    from dream.core.capabilities import capability_report
    report = capability_report(provider_metadata={'model': 'vision-tool-genius', 'base_url': 'http://localhost'},
                               settings={'context_limit': 4096, 'parallel': 4, 'vision': True})
    for key in ('vision', 'tool_calling', 'context_tokens', 'concurrency', 'cancellation', 'reasoning_levels'):
        assert report[key] == {'known': False, 'value': None, 'source': 'unreported'}
    assert report['configured']['context_limit'] == 4096


def test_reported_machx_ladder_drives_performance_without_inventing_medium():
    from dream.core.capabilities import capability_report, ordered_reasoning_levels
    from dream.core.performance import PerformanceModes
    report = capability_report(machx_capabilities={
        'load': ['reasoning_effort'], 'defaults': {'reasoning_effort': 'max'},
        'reasoning': {'effort_levels': ['low', 'high', 'max']}},
        machx_props={'default_generation_settings': {'n_ctx': 8192}})
    modes = PerformanceModes(16384, 'max', effort_levels=ordered_reasoning_levels(report)).status()['modes']
    assert [row['reasoning_effort'] for row in modes[1:]] == ['low', 'high', 'max']
    assert report['context_tokens']['value'] == 8192
    assert report['reasoning_levels']['source'] == 'machx.capabilities.reasoning.effort_levels'


@pytest.mark.parametrize('levels', [['ultra'], ['low', 'low'], 'low', [True], [None]])
def test_invalid_machx_ladder_is_unknown_with_warning(levels):
    from dream.core.capabilities import capability_report, ordered_reasoning_levels
    report = capability_report(machx_capabilities={'load': ['reasoning_effort'], 'reasoning': {'effort_levels': levels}})
    assert ordered_reasoning_levels(report) == ()
    assert report['warnings']


def test_explicit_false_and_conflicts_are_not_silently_replaced():
    from dream.core.capabilities import capability_report
    report = capability_report(provider_metadata={'vision': False, 'tool_calling': True, 'concurrency': 2,
        'cancellation': False, 'context_tokens': 4096, 'reasoning_levels': ['minimal', 'high']},
        machx_props={'default_generation_settings': {'n_ctx': 8192}})
    assert report['vision'] == {'known': True, 'value': False, 'source': 'provider.metadata.vision'}
    assert report['context_tokens']['value'] is None
    assert 'conflicting.context_tokens' in report['warnings']
    assert report['reasoning_levels']['value'] == ['minimal', 'high']


@pytest.mark.parametrize('metadata', [False, [], 'secret-token'])
def test_malformed_metadata_is_bounded_and_does_not_echo_input(metadata):
    from dream.core.capabilities import capability_report
    report = capability_report(provider_metadata=metadata, machx_props={'default_generation_settings': []})
    assert report['warnings']
    assert 'secret-token' not in str(report)
