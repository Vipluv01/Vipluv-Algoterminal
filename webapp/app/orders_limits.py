"""A single named constant instead of a magic number buried in a Pydantic
Field() call -- so the actual configured value can be imported and
displayed on the Risk page (app/routers/risk.py) instead of drifting out
of sync with a second, hand-copied number there."""

MAX_ORDER_QTY = 100_000
