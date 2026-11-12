from pathlib import Path
import pytest
from firmware_machine import FirmwareMachine,ROOT
from shamaos.flashfs import FileType,ShamaFS
from shamaos.assembler import assemble

A,B,UP,DOWN,LEFT,RIGHT=0x14,0x15,0x10,0x11,0x12,0x13


def editor_file(name='notes', program=False):
    m=FirmwareMachine('editor');m.settle()
    if program:m.event(DOWN)
    m.event(A);m.type(name);m.event(1,13)
    assert m.regs[8]==3
    return m


def action(m,index):
    m.event(B)
    for _ in range(index):m.event(DOWN)
    assert m.views[-1][1][1]==index
    m.event(A)


def test_editor_inserts_deletes_moves_between_lines_and_saves_exact_bytes():
    m=editor_file();m.type('ABC');m.event(LEFT);m.event(LEFT);m.type('x')
    assert bytes(m.memory[0xc0000:0xc0004])==b'AxBC'
    m.event(1,8);m.event(1,127)
    assert bytes(m.memory[0xc0000:0xc0002])==b'AC'
    m.type('\n');m.event(A)
    assert m.fs.read_file('notes.TXT')==b'A\nC'
    assert m.word(0xb1000+8)==3
    assert m.word(0xb1000+20)==0
    assert m.word(0xb1000+36)%2==0


def test_editor_filename_extension_assemble_and_run_and_last_good_binary():
    m=editor_file('hello',True);m.type('LDI r1 42\nHLT\n')
    action(m,4)
    good=assemble('LDI r1 42\nHLT\n').to_bytes()
    assert m.fs.read_file('hello.BIN')==good
    assert not m.fs.exists('hello.ABIN')
    # An unknown mnemonic after a good save must not replace the executable.
    m.type('BROKEN\n');action(m,4)
    assert m.regs[8]==8
    assert m.fs.read_file('hello.BIN')==good
    assert m.word(0xb1000+32)!=0


def test_delete_initially_selects_cancel_then_requires_selecting_delete():
    m=editor_file();m.type('keep');m.event(A)
    action(m,3);assert m.regs[9]==0
    m.event(A)
    assert m.fs.read_file('notes.TXT')==b'keep'
    action(m,3);m.event(RIGHT);m.event(A)
    assert not m.fs.exists('notes.TXT')


def test_newline_at_source_capacity_is_rejected_without_overrun():
    m=editor_file()
    m.regs[7]=m.regs[15]=65535
    m.memory[0xcffff]=0xa5
    m.event(1,13)
    assert m.regs[7]==65535 and m.memory[0xcffff]==0xa5
    assert m.regs[8]==8


def test_editor_save_as_rename_and_cancel_new_preserve_prior_document():
    m=editor_file('original');m.type('content\n');m.event(A)
    action(m,1);m.type('copy');m.event(1,13)
    assert m.fs.read_file('original.TXT')==b'content\n'
    assert m.fs.read_file('copy.TXT')==b'content\n'
    action(m,2);m.type('renamed');m.event(1,13)
    assert m.fs.read_file('renamed.TXT')==b'content\n' and not m.fs.exists('copy.TXT')
    action(m,7);m.event(B)  # cancel New Program, returning to the TXT document
    assert m.regs[11]==int(FileType.TXT) and m.regs[8]==3
    m.type('kept');m.event(A)
    assert m.fs.read_file('renamed.TXT')==b'content\nkept'


@pytest.mark.parametrize('filename',['editor.asm','files.asm','miner.asm','desktop.asm','calculator.asm','monitor.asm','paint.asm','settings.asm','terminal.asm'])
def test_files_to_editor_preserves_all_shipped_source_and_transfers_owned_handle(filename):
    source=(ROOT/'src/shamaos/firmware'/filename).read_bytes()
    if filename in ('editor.asm','files.asm','miner.asm'):
        assert len(source)>4096
    fs=ShamaFS();fs.create_file('editor.asm',source,FileType.ASM)
    m=FirmwareMachine('files',fs);m.settle();m.event(A)
    assert m.pending_app==1
    assert m.word(0xb1008)==len(source)
    assert bytes(m.memory[0xc0000:0xc0000+len(source)])==source
    handle=m.word(0xb1000)
    assert fs.read_handle(handle,len(source),owner=1)==source
    fs.close_owner(2)
    m.load_app('editor');m.settle();m.type(';');m.event(A)
    assert fs.read_file('editor.asm')==b';'+source


def test_files_rejects_oversized_source_without_truncating_or_opening_editor():
    data=b'N'*65536
    fs=ShamaFS();fs.create_file('large.asm',data,FileType.ASM)
    m=FirmwareMachine('files',fs);m.settle();m.event(A)
    assert m.pending_app is None and m.regs[9]==4
    assert fs.read_file('large.asm')==data


def test_assembly_reports_explicit_line_code_and_does_not_overwrite_other_file_type():
    m=editor_file('hello',True);m.type('HLT\n')
    m.fs.create_file('hello.BIN',b'not an executable',FileType.TXT)
    action(m,4)
    assert m.regs[8]==8 and m.word(0xb1020)==11
    assert m.fs.read_file('hello.BIN')==b'not an executable'
    rendered=[data for op,args,data in m.draws if op==12]
    assert b'LINE 00000  CODE 0B' in rendered
    m.event(B);m.event(B);m.type(';');m.event(A)
    assert m.fs.read_file('hello.ASM')==b'HLT\n;'


@pytest.mark.parametrize('source_name,binary_name',[('program','program.BIN'),('self.BIN',None)])
def test_assembly_handles_source_without_extension_and_rejects_source_binary_collision(source_name,binary_name):
    fs=ShamaFS();source=b'LDI r1 123\nHLT\n';fs.create_file(source_name,source,FileType.ASM)
    m=FirmwareMachine('files',fs);m.settle();m.event(A)
    m.load_app('editor');m.settle();action(m,4)
    assert fs.read_file(source_name)==source
    if binary_name:
        assert fs.read_file(binary_name)==assemble(source.decode()).to_bytes()
    else:
        assert m.regs[8]==8 and m.word(0xb1020)==11
        m.event(B);m.event(B);m.type(';');m.event(A)
        assert fs.read_file(source_name)==b';'+source


def test_files_delete_cancel_and_real_folder_creation():
    fs=ShamaFS();fs.create_file('a.txt',b'intact',FileType.TXT)
    m=FirmwareMachine('files',fs);m.settle();m.event(RIGHT);m.event(A)
    assert fs.read_file('a.txt')==b'intact'
    m.event(1,ord('N'));m.type('docs');m.event(1,13)
    assert fs.stat('docs').file_type==FileType.DIR


def test_explorer_renders_type_and_full_size_metadata():
    fs=ShamaFS();fs.create_file('program.asm',b'NOP\n'*1600,FileType.ASM)
    m=FirmwareMachine('files',fs);m.settle()
    rendered=b' '.join(data for op,args,data in m.draws if op==12)
    assert b'program.asm' in rendered
    assert b'ASM SIZE 00001900' in rendered


def selected_name(machine):
    return machine.cstring(0xb0000) if machine.regs[10] else None


def select_file(machine,name):
    for _ in range(64):
        if selected_name(machine)==name:return
        machine.event(DOWN)
    raise AssertionError(f'could not select {name!r}; selected {selected_name(machine)!r}')


def test_explorer_enters_folders_filters_direct_children_and_returns_to_parent():
    fs=ShamaFS();fs.create_file('a.txt',b'root',FileType.TXT)
    fs.create_file('docs',file_type=FileType.DIR)
    fs.create_file('docs/child.asm',b'HLT\n',FileType.ASM)
    fs.create_file('docs/sub',file_type=FileType.DIR)
    fs.create_file('docs/sub/nested.txt',b'nested',FileType.TXT)
    fs.create_file('z.txt',b'last root entry',FileType.TXT)
    m=FirmwareMachine('files',fs);m.settle()
    assert selected_name(m)=='a.txt'
    m.event(DOWN);assert selected_name(m)=='docs'
    m.event(DOWN);assert selected_name(m)=='z.txt'  # descendants are hidden at root
    m.event(UP);m.event(A)
    assert selected_name(m)=='docs/child.asm'
    assert m.word(0xb1028)==len('docs/')
    m.event(DOWN);assert selected_name(m)=='docs/sub'
    m.event(A);assert selected_name(m)=='docs/sub/nested.txt'
    m.event(B);assert selected_name(m)=='docs/child.asm'
    m.event(B);assert selected_name(m)=='a.txt'
    m.event(B);assert m.pending_app==0


def test_explorer_initial_search_finds_current_directory_children_case_insensitively():
    fs=ShamaFS();fs.create_file('a.txt',b'root',FileType.TXT)
    fs.create_file('docs',file_type=FileType.DIR)
    fs.create_file('docs/X.txt',b'nested',FileType.TXT)
    fs.create_file('X.TXT',b'root X',FileType.TXT)
    m=FirmwareMachine('files',fs);m.settle();m.event(1,ord('x'))
    assert selected_name(m)=='X.TXT'
    m.event(1,ord('D'));m.event(A);m.event(1,ord('x'))
    assert selected_name(m)=='docs/X.txt'
    m.event(1,ord('Z'));assert selected_name(m)=='docs/X.txt'


def test_explorer_creates_and_renames_relative_names_inside_folder():
    fs=ShamaFS();fs.create_file('docs',file_type=FileType.DIR)
    fs.create_file('docs/old.txt',b'keep exact bytes',FileType.TXT)
    m=FirmwareMachine('files',fs);m.settle();m.event(A)
    m.event(1,ord('n'));m.type('new');m.event(1,13)
    assert fs.stat('docs/new').file_type==FileType.DIR and not fs.exists('new')
    select_file(m,'docs/old.txt')
    m.event(1,ord('r'));m.type('renamed.txt');m.event(1,13)
    assert fs.read_file('docs/renamed.txt')==b'keep exact bytes'
    assert not fs.exists('docs/old.txt')
    m.event(B);assert selected_name(m)=='docs'
    m.event(RIGHT);m.event(RIGHT);m.event(A)
    assert m.regs[9]==4  # nonempty folder cannot be deleted
    assert fs.read_file('docs/renamed.txt')==b'keep exact bytes'


def test_explorer_text_view_respects_lines_and_scrolls_without_skipping_short_lines():
    fs=ShamaFS();fs.create_file('readme.txt',b'first\nsecond\n\nthird\nfourth\nfifth\nsixth\nseventh\neighth\n',FileType.TXT)
    m=FirmwareMachine('files',fs);m.settle();m.draws.clear();m.event(A)
    rows=[data for op,args,data in m.draws if op==12 and args[3]>=60]
    assert rows[:3]==[b'first',b'second',b'third']
    m.draws.clear();m.event(DOWN)
    assert m.regs[7]==6
    rows=[data for op,args,data in m.draws if op==12 and args[3]>=60]
    assert rows[0]==b'second' and b'eighth' in rows
    m.event(DOWN);assert m.regs[7]==13
    m.event(DOWN);assert m.regs[7]==14
    m.event(UP);assert m.regs[7]==13
    m.event(UP);assert m.regs[7]==6
    m.event(UP);assert m.regs[7]==0


def test_settings_survive_power_cycle_and_paint_restores_cursor_style():
    settings=FirmwareMachine('settings');settings.settle()
    settings.event(DOWN);settings.event(A)  # cross -> point, keep enabled
    data=settings.fs.read_file('settings.cfg')
    assert int.from_bytes(data[4:8],'little')==3
    restored=ShamaFS.deserialize(settings.fs.serialize())
    again=FirmwareMachine('settings',restored);again.settle()
    assert again.regs[11]==3
    paint=FirmwareMachine('paint',restored);paint.settle()
    cursors=[args for op,args,data in paint.draws if op==0x12]
    assert cursors[-1][:3]==[160,90,3]
    before=len(paint.draws);paint.event(RIGHT)
    changed=paint.draws[before:]
    assert [args for op,args,data in changed if op==0x12][-1][:3]==[161,90,3]
    assert not any(op in (1,2,3,4,0x13,0x14) for op,args,data in changed)
    paint.event(A)
    ops=[op for op,args,data in paint.draws]
    assert ops[-5:]==[0x13,2,0x14,0x12,0x15]


@pytest.mark.parametrize(('command','app'), [('ls',2),('edit',1),('mem',4),('miner',3),('paint',7),('calc',6)])
def test_terminal_matches_complete_case_insensitive_commands(command,app):
    m=FirmwareMachine('terminal');m.settle();m.type(command);m.event(1,13)
    assert m.pending_app==app


@pytest.mark.parametrize('command',['L','LST','EDITOR','MINERAL','FLASHLIGHT','RAM','RUNX'])
def test_terminal_rejects_unknown_commands_instead_of_matching_prefixes(command):
    m=FirmwareMachine('terminal');m.settle();m.type(command);m.event(1,13)
    assert m.pending_app is None
    assert m.views[-1][1][0]==1
