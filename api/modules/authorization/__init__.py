"""Authorization: the gate every financial action passes through.

Kept bare for the same reason as ``modules.risk`` - the service imports the
policy engine and the risk service, and re-exporting it from the package would
turn that into a cycle the moment anything imports the package for a type.
"""
