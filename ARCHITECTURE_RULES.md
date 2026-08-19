# ARCHITECTURE_RULES

## Repository Structure
```
Project-alpha-node/
├── agents/
├── shared/
├── tests/
├── .github/
```

## Rules
- `shared/` is frozen unless an approved production bug fix is required.
- New agents live in `agents/anXX/`.
- Tests live in `tests/`.
- Cross-agent imports use absolute imports.
- Internal agent imports use relative imports.
- Every completed agent must pass GitHub Actions before being frozen.
