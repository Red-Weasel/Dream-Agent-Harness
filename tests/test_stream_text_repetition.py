"""Behavior fixtures for a bounded guard, without a model or transport."""
import hashlib

import pytest

from dream.core.stream_text_repetition import TextRepetitionDetector

PROMISE = 'Now I will write the complete result file with all the verified findings.'


@pytest.mark.parametrize('width', [1, 2, 7, 43, 4096])
@pytest.mark.parametrize('channel', ['content', 'reasoning_content'])
def test_repeated_promise_cuts_independently_of_network_chunking(width, channel):
    detector = TextRepetitionDetector()
    text = (PROMISE + '\n\n') * 6
    for start in range(0, len(text), width):
        cut = detector.observe(channel, text[start:start + width])
        if cut:
            break
    assert cut is not None
    assert cut.channel == channel and cut.occurrences == 6
    assert cut.line_bytes == len(PROMISE.encode())
    assert cut.line_sha256 == hashlib.sha256(PROMISE.encode()).hexdigest()
    assert not hasattr(cut, 'text')


def test_channels_cannot_add_together_to_falsely_cut_a_reply():
    detector = TextRepetitionDetector()
    for _ in range(3):
        assert detector.observe('content', PROMISE + '\n') is None
        assert detector.observe('reasoning_content', PROMISE + '\n') is None
    assert detector.observe('content', PROMISE + '\n') is None


@pytest.mark.parametrize('block', [
    '```python\n' + (PROMISE + '\n') * 20 + '```\n',
    '~~~text\n' + (PROMISE + '\n') * 20 + '~~~\n',
    ('> ' + PROMISE + '\n') * 20,
    ('    ' + PROMISE + '\n') * 20,
    ('\t' + PROMISE + '\n') * 20,
    ('A short refrain.\n') * 200,
])
def test_code_quotes_and_short_refrains_are_not_a_loop(block):
    detector = TextRepetitionDetector()
    for character in block:
        assert detector.observe('content', character) is None


def test_new_information_breaks_a_consecutive_repetition_run():
    detector = TextRepetitionDetector()
    for index in range(20):
        assert detector.observe('content', (PROMISE + '\n') * 5) is None
        assert detector.observe('content', f'New source inspected and verified: item {index}.\n') is None


def test_long_lines_are_discarded_without_keeping_unbounded_reply_text():
    detector = TextRepetitionDetector()
    for _ in range(100):
        assert detector.observe('reasoning_content', 'x' * 1000) is None
        assert detector.retained_chars <= 4096
    assert detector.observe('reasoning_content', '\n') is None
    assert detector.observe('reasoning_content', (PROMISE + '\n') * 6) is not None


def test_unknown_channels_and_malformed_fragments_cannot_supply_a_cut():
    detector = TextRepetitionDetector()
    for channel, fragment in [('tool_calls', (PROMISE + '\n') * 20),
                              ('tool_call_preview', (PROMISE + '\n') * 20),
                              ('content', None), ('reasoning_content', 12),
                              ([], PROMISE + '\n'), (None, PROMISE + '\n')]:
        assert detector.observe(channel, fragment) is None


@pytest.mark.parametrize('options', [
    {'threshold': True}, {'threshold': 1}, {'threshold': 1000},
    {'min_chars': 0}, {'min_chars': 4097}, {'max_chars': 100_000},
    {'max_chars': 48, 'min_chars': 49},
])
def test_configuration_is_strict_and_bounded(options):
    with pytest.raises(ValueError):
        TextRepetitionDetector(**options)


@pytest.mark.parametrize('channel', ['content', 'reasoning_content'])
def test_unicode_crlf_counts_characters_and_hashes_actual_utf8(channel):
    line = '結果🙂' * 16  # Exactly 48 characters, substantially more UTF-8 bytes.
    detector = TextRepetitionDetector()
    for occurrence in range(6):
        for character in line + '\r':
            assert detector.observe(channel, character) is None
        cut = detector.observe(channel, '\n')
        if occurrence < 5:
            assert cut is None
    assert cut is not None and cut.occurrences == 6
    assert cut.line_sha256 == hashlib.sha256(line.encode('utf-8')).hexdigest()
    assert cut.line_bytes == len(line.encode('utf-8'))


@pytest.mark.parametrize('length', [47, 48])
def test_default_minimum_and_completed_line_boundary_are_exact(length):
    detector = TextRepetitionDetector()
    line = 'x' * length
    assert detector.observe('content', (line + '\n') * 5 + line) is None
    cut = detector.observe('content', '\n')
    assert (cut is not None) is (length == 48)


@pytest.mark.parametrize('blank_lines', [0, 1, 2, 3, 8])
def test_only_up_to_two_blank_lines_preserve_a_repetition_run(blank_lines):
    detector = TextRepetitionDetector()
    assert detector.observe('content', (PROMISE + '\n') * 5) is None
    assert detector.observe('content', '\n' * blank_lines) is None
    cut = detector.observe('content', PROMISE + '\n')
    assert (cut is not None) is (blank_lines <= 2)
    if blank_lines > 2:
        assert detector.observe('content', (PROMISE + '\n') * 4) is None
        assert detector.observe('content', PROMISE + '\n').occurrences == 6


@pytest.mark.parametrize('opening,inner,closing', [
    ('````python', '```', '````'),
    ('```python', '~~~', '```'),
    ('~~~~text', '~~~', '~~~~'),
    ('~~~text', '```', '~~~'),
    ('```python', '``` not a closing fence', '```'),
    ('```python', '    ```', '```'),
    ('~~~text', '\t~~~', '~~~'),
])
def test_nested_or_mismatched_fences_do_not_release_ignored_content(opening, inner, closing):
    detector = TextRepetitionDetector()
    assert detector.observe('content', (PROMISE + '\n') * 5) is None
    block = opening + '\n' + inner + '\n' + (PROMISE + '\n') * 12
    for character in block:
        assert detector.observe('content', character) is None
    assert detector.observe('content', closing + '\n') is None
    assert detector.observe('content', (PROMISE + '\n') * 5) is None
    assert detector.observe('content', PROMISE + '\n').occurrences == 6


@pytest.mark.parametrize('marker', ['    ```python', '\t~~~text', '> ```python'])
def test_indented_or_quoted_fence_marker_cannot_open_a_prose_fence(marker):
    detector = TextRepetitionDetector()
    assert detector.observe('content', marker + '\n') is None
    assert detector.observe('content', (PROMISE + '\n') * 5) is None
    assert detector.observe('content', PROMISE + '\n') is not None


@pytest.mark.parametrize('marker', ['```', '~~~'])
def test_up_to_three_spaces_still_allow_real_fences(marker):
    detector = TextRepetitionDetector()
    assert detector.observe('content', '   ' + marker + 'text\n') is None
    assert detector.observe('content', (PROMISE + '\n') * 12) is None
    assert detector.observe('content', '   ' + marker + '\n') is None
    assert detector.observe('content', (PROMISE + '\n') * 5) is None
    assert detector.observe('content', PROMISE + '\n') is not None


def test_channel_line_buffers_counts_and_fences_are_independent():
    detector = TextRepetitionDetector()
    for _ in range(5):
        assert detector.observe('content', PROMISE + '\n') is None
        assert detector.observe('reasoning_content', PROMISE + '\n') is None
    assert detector.observe('reasoning_content', '```\n' + (PROMISE + '\n') * 12) is None
    cut = detector.observe('content', PROMISE + '\n')
    assert cut.channel == 'content' and cut.occurrences == 6


def test_partial_lines_cannot_be_completed_by_the_other_channel():
    detector = TextRepetitionDetector()
    for _ in range(12):
        assert detector.observe('content', PROMISE) is None
        assert detector.observe('reasoning_content', '\n') is None
    assert detector.observe('content', '\n') is None
    assert detector.observe('content', (PROMISE + '\n') * 5) is None
    assert detector.observe('content', PROMISE + '\n') is not None


def test_oversized_line_erases_prior_count_and_both_buffers_stay_bounded():
    detector = TextRepetitionDetector()
    for channel in ('content', 'reasoning_content'):
        assert detector.observe(channel, (PROMISE + '\n') * 5 + 'x' * 4096) is None
    assert detector.retained_chars == 2 * 4096
    for channel in ('content', 'reasoning_content'):
        assert detector.observe(channel, 'x' * 8193 + '\n') is None
        assert detector.retained_chars <= 2 * 4096
        assert detector.observe(channel, (PROMISE + '\n') * 5) is None
    assert detector.retained_chars == 0
    cut = detector.observe('reasoning_content', PROMISE + '\n')
    assert cut.channel == 'reasoning_content' and cut.occurrences == 6


def test_different_long_lines_never_accumulate_as_one_refrain():
    detector = TextRepetitionDetector()
    for index in range(100):
        assert detector.observe('content', f'{PROMISE} Source number {index}.\n') is None


def test_spent_cut_is_immutable_digest_metadata_and_never_consumes_more_input():
    from dataclasses import FrozenInstanceError, asdict

    detector = TextRepetitionDetector()
    cut = detector.observe('content', (PROMISE + '\n') * 6 + 'ignored trailing text')
    assert asdict(cut) == dict(channel='content', occurrences=6,
        line_sha256=hashlib.sha256(PROMISE.encode()).hexdigest(), line_bytes=len(PROMISE.encode()))
    retained = detector.retained_chars
    for channel, fragment in [('reasoning_content', 'y' * 100_000),
                              ('content', (PROMISE + '\n') * 20),
                              ([], {}), ('tool_calls', None)]:
        assert detector.observe(channel, fragment) is cut
        assert detector.retained_chars == retained
    with pytest.raises(FrozenInstanceError):
        cut.occurrences = 7


@pytest.mark.parametrize('opening,closing', [
    ('<invoke name="write_file">', '</invoke>'),
    ('<function=write_file>', '</function>'),
    ('<tool_calls>', '</tool_calls>'),
])
@pytest.mark.parametrize('fragment_size', [1, 13])
def test_raw_xml_tool_arguments_are_excluded_until_matching_close(opening,closing,fragment_size):
    detector = TextRepetitionDetector()
    text = opening + '\n<parameter name="body">\n' + (PROMISE + '\n') * 12
    text += '</parameter>\n' + closing + '\n'
    for start in range(0,len(text),fragment_size):
        assert detector.observe('content',text[start:start+fragment_size]) is None
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


def test_raw_xml_outer_wrapper_survives_inner_call_close():
    detector = TextRepetitionDetector()
    assert detector.observe('content','<tool_calls>\n<invoke name="write_file">\n') is None
    assert detector.observe('content',(PROMISE+'\n')*12) is None
    assert detector.observe('content','</invoke>\n'+(PROMISE+'\n')*12) is None
    assert detector.observe('content','</tool_calls>\n'+(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


def test_complete_inline_xml_call_does_not_hide_following_prose():
    detector = TextRepetitionDetector()
    xml = '<invoke name="read_file"><parameter name="path">generic.txt</parameter></invoke>\n'
    assert detector.observe('content',xml) is None
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('fragment_size', [1, 13, None])
@pytest.mark.parametrize('opening,closing', [
    ('<invoke name="write_file"><parameter name="body">'+'x'*5000, '</parameter></invoke>'),
    ('<invoke'+' '*5000+'name="write_file">', '</invoke>'),
    ('<invoke name="write_file">', 'x'*5000+'</invoke>'),
    ('```'+'info'*1500, '```'),
    ('~~~text', '~~~'+' '*5000),
    ('```text', '`'*5000),
    ('<invoke\nname="write_file">', '</invoke>'),
    ('x'*4100+'<invoke name="write_file">', '</invoke>'),
    ('x'*4093+'<invoke name="write_file">', '</invoke>'),
    ('<invoke name="write_file">', '</invoke\n>'),
], ids=['xml-long-body-opening','xml-long-attribute-gap','xml-long-closing-line',
        'fence-long-info','fence-close-long-padding','fence-long-closing-marker','xml-newline-name',
        'xml-opener-after-buffer','xml-opener-bisected-at-buffer','xml-close-newline-bracket'])
def test_framing_survives_oversized_lines_and_split_tag_names(opening,closing,fragment_size):
    detector = TextRepetitionDetector()
    text = opening+'\n'+(PROMISE+'\n')*12+closing+'\n'
    width = fragment_size or len(text)
    for start in range(0,len(text),width):
        assert detector.observe('content',text[start:start+width]) is None
        assert detector.retained_chars <= 2*4096
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('fragment_size',[1,13,None])
def test_huge_fence_marker_requires_the_full_opening_width_to_close(fragment_size):
    detector=TextRepetitionDetector()
    text='`'*5000+'\n'+(PROMISE+'\n')*8+'`'*4500+'\n'+(PROMISE+'\n')*8+'`'*5000+'\n'
    width=fragment_size or len(text)
    for start in range(0,len(text),width):
        assert detector.observe('content',text[start:start+width]) is None
        assert detector.retained_chars<=2*4096
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('prefix',['    ','\t','> '])
@pytest.mark.parametrize('marker',['`','~'])
def test_huge_indented_or_quoted_marker_neither_opens_nor_closes_a_fence(prefix,marker):
    excluded=prefix+marker*5000+'\n'
    detector=TextRepetitionDetector()
    for start in range(0,len(excluded),13):
        assert detector.observe('content',excluded[start:start+13]) is None
        assert detector.retained_chars<=2*4096
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None
    detector=TextRepetitionDetector()
    assert detector.observe('content',marker*3+'text\n') is None
    for start in range(0,len(excluded),13):
        assert detector.observe('content',excluded[start:start+13]) is None
        assert detector.retained_chars<=2*4096
    assert detector.observe('content',(PROMISE+'\n')*12) is None
    assert detector.observe('content',marker*3+'\n'+(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('marker',['```','~~~'])
@pytest.mark.parametrize('fragment_size',[13,None])
def test_oversized_xml_marker_inside_fenced_code_does_not_poison_later_prose(marker,fragment_size):
    detector=TextRepetitionDetector()
    text=marker+'text\n'+'x'*5000+'<invoke name="write_file">\n'+(PROMISE+'\n')*12+marker+'\n'
    width=fragment_size or len(text)
    for start in range(0,len(text),width):
        assert detector.observe('content',text[start:start+width]) is None
        assert detector.retained_chars<=2*4096
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('prefix',['    ','\t','> '])
def test_oversized_quoted_or_indented_xml_marker_does_not_open_a_raw_call(prefix):
    detector=TextRepetitionDetector()
    text=prefix+'x'*5000+'<invoke name="write_file">\n'
    for start in range(0,len(text),13):
        assert detector.observe('content',text[start:start+13]) is None
        assert detector.retained_chars<=2*4096
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('marker',['`','~'])
def test_saturated_fence_width_never_guesses_a_later_close(marker):
    detector=TextRepetitionDetector()
    for width in (1_000_001,1_000_002):
        for start in range(0,width,4096):
            assert detector.observe('content',marker*min(4096,width-start)) is None
            assert detector.retained_chars<=2*4096
        assert detector.observe('content','\n'+(PROMISE+'\n')*12) is None
    assert detector.observe('reasoning_content',(PROMISE+'\n')*5) is None
    assert detector.observe('reasoning_content',PROMISE+'\n') is not None


@pytest.mark.parametrize('literal', ['</invoke\nnot a closing bracket\n',
                                    '</invoke =notclose\n',
                                    '</invoke'+' '*5000+'\nnotclose\n'],
                         ids=['newline-literal','invalid-attribute','oversized-incomplete-close'])
@pytest.mark.parametrize('closing',['</invoke>\n','</invoke\n'+' '*5000+'\n>\n'],
                         ids=['ordinary-close','long-whitespace-close'])
@pytest.mark.parametrize('fragment_size',[1,13,None])
def test_raw_xml_requires_a_complete_valid_closing_tag(literal,closing,fragment_size):
    detector=TextRepetitionDetector()
    text='<invoke name="write_file">\n<parameter name="body" string="true">\n'
    text+=literal+(PROMISE+'\n')*8+'</parameter>\n'+closing
    width=fragment_size or len(text)
    for start in range(0,len(text),width):
        assert detector.observe('content',text[start:start+width]) is None
        assert detector.retained_chars<=2*4096
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('marker',['```','~~~'])
@pytest.mark.parametrize('fragment_size',[1,13,None])
def test_real_fence_opener_takes_precedence_over_xml_in_its_info_string(marker,fragment_size):
    detector=TextRepetitionDetector()
    text=marker+'xml title="<invoke name=example>"\n</invoke>\n'+(PROMISE+'\n')*8+marker+'\n'
    width=fragment_size or len(text)
    for start in range(0,len(text),width):
        assert detector.observe('content',text[start:start+width]) is None
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None


@pytest.mark.parametrize('marker',['```','~~~'])
@pytest.mark.parametrize('fragment_size',[1,13,None])
def test_fence_like_line_inside_real_xml_argument_cannot_capture_later_prose(marker,fragment_size):
    detector=TextRepetitionDetector()
    text='<invoke name="write_file">\n<parameter name="body">\n'+marker+'text\n'
    text+=(PROMISE+'\n')*8+'</parameter>\n</invoke>\n'
    width=fragment_size or len(text)
    for start in range(0,len(text),width):
        assert detector.observe('content',text[start:start+width]) is None
    assert detector.observe('content',(PROMISE+'\n')*5) is None
    assert detector.observe('content',PROMISE+'\n') is not None
