# Physical Controls and GUI Navigation

## Power

- ON/OFF control.
- reset/debug control may be physically separate.
- OFF initiates clean shutdown before clock gating when OS is running.

## Controller

Minimum physical controls:

```text
        UP
   LEFT  +  RIGHT
       DOWN

[A] Confirm/Open
[B] Back/Cancel

[HOME]
[EXIT]
[EDITOR]
[FILES]
```

## Universal behavior

- HOME: close current app normally and return to Desktop.
- EXIT: close current app normally.
- EDITOR: close current app and launch Editor.
- FILES: close current app and launch File Explorer.
- A: activate current selection.
- B: cancel/back.
- D-pad: move selection/cursor.

## Keyboard

Keyboard input is required for:

- Editor text files.
- assembly source.
- filenames/rename.
- terminal commands.

A keyboard matrix/encoder converts keypresses to character/control codes. The OS input queue latches events so short physical presses are not lost between CPU polls.

## Delete confirmation

File/application delete action opens:

```text
Delete "name"?

[ Cancel ]     [ Delete ]
```

Default focus should be Cancel. Destruction occurs only after explicit Delete/Confirm.

## Miner

Miner app controller actions include:

- Start.
- Stop.
- previous saved generation/run.
- next saved generation/run.
- reset/new generation.
- Exit.

## Editor

Controller navigates menus and cursor; keyboard enters content.

Editor top-level actions:

- New Text File.
- New Program.
- Open.
- Save.
- Save As.
- Rename.
- Delete.
- Run/Assemble.
- Exit.
