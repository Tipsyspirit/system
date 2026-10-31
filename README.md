# THE SYSTEM

Status window where every stat increase traces to a logged real-world action.

    pip install -r requirements.txt
    python run.py            # http://127.0.0.1:8000
    python -m pytest -q backend/tests

## Layout
- `rules.txt`  single source of truth. Backend refuses to boot if an invariant is broken.
- `backend/app/rules.py`  loads and validates rules.txt
- `backend/app/engine.py`  pure game logic (XP, caps, levels, titles). Never imports the model.
- `backend/app/ml.py`  per-player online logistic regression. Advises only. Evolves with step caps, snapshots and rollback.
- `backend/app/main.py`  FastAPI + SQLite
- `frontend/`  your UI, rendering state from the API

## Changing a rule
Edit a number in `rules.txt`, restart. Every logged event stores the first 12 chars of the rules hash it was scored under.
