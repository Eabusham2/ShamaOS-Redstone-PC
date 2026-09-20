# Contributing

## Non-negotiable project rules

Before changing architecture, read:

- `docs/INITIAL_SPEC_AND_TRANSCRIPTS.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/ARCHITECTURE.md`
- `docs/VALIDATION.md`

Do not silently replace real redstone computation with external runtime computation.

Do not report logical/banked memory as literal physical cell capacity.

Do not remove an agreed final feature merely to make an intermediate milestone easier.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

World-writing dependencies are optional:

```bash
pip install -e ".[dev,world]"
```

## Branch policy

The owner requested a single primary branch. Work should target `main` unless the owner explicitly authorizes another branch/workflow.

## Tests

Every ISA encoding, SHA primitive, filesystem format or physical-layout rule change must add/update tests and documentation together.
