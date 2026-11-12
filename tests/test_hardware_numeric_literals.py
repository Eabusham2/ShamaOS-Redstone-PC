"""Host/RTL parity for the scanner's shared numeric accumulator."""
import random

import pytest

from test_hardware_assembler import rtl_assembler, run_case


def test_all_numeric_bases_signs_boundaries_and_48_character_tokens(rtl_assembler, tmp_path):
    values = [0, 1, 9, 10, 15, 16, 31, 127, 128, 255, 256, 32767,
              32768, 65535, 65536, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]
    values += [random.Random(27539 + i).randrange(1 << 32) for i in range(64)]
    literals = []
    for value in values:
        for digits, prefix in ((str(value), ''), (f'{value:x}', '0x'), (f'{value:b}', '0b')):
            literals.extend((prefix + digits, '+' + prefix + digits,
                             '+' + prefix + '0' * (47 - len(prefix) - len(digits)) + digits))
            if value <= 0x80000000:
                literals.append('-' + prefix + digits)
    # Alternate quoted and symbolic values with numeric fields so per-token
    # flags cannot leak from one base, sign, quote or token kind into the next.
    source = 'define MAXIMUM +0xFFFFFFFF\n'
    source += ''.join(f'LDI r1 {value}\n' for value in literals)
    source += "LDI r2 '\\xff'\nLDI r3 MAXIMUM\nLDI r4 -0\nLDI r5 0B1\nHLT\n"
    run_case(rtl_assembler, tmp_path, source)


@pytest.mark.parametrize('literal', [
    '+', '-', '0x', '0X', '0b', '0B', '+0x', '-0x', '+0b', '-0b',
    '0xG', '0x1G', '0b2', '0b12', '0b10A', '+1A', '-1A',
    '00x1', '00b1', '+00x1', '-00b1', '+-1', '-+1', '--1', '++1',
    '0x100000000', '+0x100000000', '-0x80000001',
    '4294967296', '+4294967296', '-2147483649',
    '0b100000000000000000000000000000000', '-0b10000000000000000000000000000001',
    '9' * 48, '0x' + 'F' * 46, '0b' + '1' * 46,
    '42949672960' + '0' * 30, '0x100000000' + '0' * 30,
    '0' * 49, '0x' + '0' * 47,
])
def test_bad_numeric_tokens_reject_with_exact_line_code(rtl_assembler, tmp_path, literal):
    run_case(rtl_assembler, tmp_path, f'NOP\nLDI r1 {literal}\n', error=2, line=2)


def test_long_label_and_definition_names_keep_full_hash_and_last_character(rtl_assembler, tmp_path):
    colon_label = 'LongLabel' + 'x' * 38
    dot_label = '.' + 'target' + 'y' * 41
    definition = 'VALUE_' + 'z' * 42
    assert len(colon_label + ':') == len(dot_label) == len(definition) == 48
    source = f'define {definition} 0x12345678\n'
    source += f'{colon_label}: LDI r1 {definition}\nJMP {dot_label}\n'
    source += f'{dot_label} LDI r2 -0x80000000\nBR.EQ {colon_label}\nHLT\n'
    run_case(rtl_assembler, tmp_path, source)


@pytest.mark.parametrize('text', [
    "LDI r1 '\\400'", "LDI r1 '\\777'", "LDI r1 '\\x100'", "LDI r1 'abcdefgh'",
])
def test_invalid_long_quoted_literals_still_reject(rtl_assembler, tmp_path, text):
    run_case(rtl_assembler, tmp_path, 'NOP\n' + text + '\n', error=2, line=2)


def test_full_label_and_definition_tables_preserve_distinct_shared_prefixes(rtl_assembler, tmp_path):
    # Names deliberately share their first eight characters. The shorter
    # physical character store must not shorten the streaming symbol hashes.
    source = ''.join(f'define DefinitionShared{i:03d} {i}\n' for i in range(128))
    source += ''.join(f'LabelShared{i:03d}: LDI r1 DefinitionShared{i:03d}\n' for i in range(128))
    source += 'JMP LabelShared127\nBR.EQ LabelShared000\nHLT\n'
    run_case(rtl_assembler, tmp_path, source)


@pytest.mark.parametrize('source,code', [
    (''.join(f'LabelShared{i:03d}: NOP\n' for i in range(129)), 3),
    (''.join(f'define DefinitionShared{i:03d} {i}\n' for i in range(129)), 4),
])
def test_129th_symbol_reports_the_original_limit(rtl_assembler, tmp_path, source, code):
    run_case(rtl_assembler, tmp_path, source, error=code, line=129)


@pytest.mark.parametrize('count,code', [(8, 6), (9, 2)])
def test_line_token_limit_is_independent_of_prefix_store(rtl_assembler, tmp_path, count, code):
    source = 'NOP\nNOP ' + ' '.join(f'token{i}' for i in range(count - 1)) + '\n'
    run_case(rtl_assembler, tmp_path, source, error=code, line=2)


def test_accelerator_accepts_entire_64kib_source_at_its_physical_endpoint(rtl_assembler, tmp_path):
    body = 'NOP\n' * 4094 + 'HLT\n'
    source = ';' + 'x' * (65536 - len(body) - 2) + '\n' + body
    assert len(source) == 65536
    run_case(rtl_assembler, tmp_path, source)
