"""Fake cleaning step for the demo project (does nothing)."""


def clean(rows):
    return [row for row in rows if row is not None]
