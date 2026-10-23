"""Independently authenticated administrative channel: NOT an executor approval.

Install this reviewed binding alongside the independently pinned standalone helper,
never from the install bundle. No authentication policy is selected in this repo.
"""


def authenticate_initial_installation(bundle_sha256):
    raise RuntimeError('independent-administrative-installation-channel-unconfigured')
