"""Risk scoring for financial actions.

Deliberately empty. Re-exporting the service here would make
``modules.risk`` import ``modules.risk.service``, which imports the policy
models - and the authorization service imports both. Keeping the package
namespace bare is what stops that from becoming an import cycle.
"""
