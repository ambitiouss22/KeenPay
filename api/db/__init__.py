"""Database layer: models, tenant-pinned sessions, tenant-scoped repositories.

Import this package as ``db.*`` (bare), never ``api.db.*``. Both spellings
resolve to the same files because pytest puts the repo root and ``api/`` on
sys.path, but they produce two separate module objects — and loading
``models.py`` twice registers every table on a second declarative registry,
which SQLAlchemy rejects with ``InvalidRequestError: Table 'orders' is already
defined``.
"""
