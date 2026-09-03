"""Catalog: the products a merchant sells.

Deliberately empty of re-exports. ``modules.catalog.service`` imports
``modules.commerce.safety``, and ``modules.commerce.flow`` imports this
service; eagerly importing either here makes that a circular import at startup.
Import the submodule you want directly.
"""
