"""False-positive controls for supplied document and recovery evidence."""
import pytest

from dream import harness_eval


def result_for(records, case_id):
    return next(case for case in harness_eval.grade(records)['cases']
                if case['id'] == case_id)


@pytest.mark.asyncio
async def test_document_budget_requires_integer_even_when_float_compares_equal():
    records = await harness_eval.run_self_test()
    case = next(c for c in records['cases'] if c['id'] == 'documents-facts')
    case['outputs']['facts']['budget_usd'] = 12500.0
    assert result_for(records, case['id'])['passed'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('inspection', ['', 'Metadata only: config.txt', 'primary=30',
                                        'primary=300\nbackup=300\n'])
async def test_recovery_inspection_must_show_both_settings(inspection):
    records = await harness_eval.run_self_test()
    case = next(c for c in records['cases'] if c['id'] == 'recovery-ambiguous-edit')
    case['tools'][2]['result']['content'] = [{'type': 'text', 'text': inspection}]
    assert result_for(records, case['id'])['passed'] is False


@pytest.mark.asyncio
async def test_refusal_inspection_and_retry_must_belong_to_same_ordered_sequence():
    records = await harness_eval.run_self_test()
    case = next(c for c in records['cases'] if c['id'] == 'recovery-ambiguous-edit')
    read, refusal, inspection, retry = case['tools']
    # An inspection before a second refusal cannot justify a later retry that
    # skipped inspection. Earlier success cannot justify the first refusal.
    case['tools'] = [retry, inspection, refusal, retry]
    assert result_for(records, case['id'])['passed'] is False


@pytest.mark.asyncio
async def test_unmodified_native_evidence_still_passes_all_fixtures():
    records = await harness_eval.run_self_test()
    assert harness_eval.grade(records)['passed'] == 5
