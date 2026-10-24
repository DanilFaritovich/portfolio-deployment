"""Infrastructure review replaces this file in the sealed installation artifact.

No production approval issuer or host/runtime attestor has been installed. This
default is intentionally unusable. The reviewed first-phase recipe lives in
scripts.production_integration.first_phase; its external authorities remain
unconfigured. No dynamic modules or request-selected ports.
"""
from scripts.production_integration.fixed_bindings import build_fixed

# Independently reviewed sealed Python input. No owner policy has been approved.
# Required fields and real wiring are defined in fixed_bindings.FixedPolicy.
POLICY = None


def build(installation):
    return build_fixed(installation, POLICY)
