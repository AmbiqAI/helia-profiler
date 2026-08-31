"""Dependency management: NSX workspaces, locks, and compatibility baselines.

``dependencies`` builds the deterministic dependency workspaces, ``sync``
verifies frozen syncs behind the lock-digest stamp, ``compatibility`` owns
the qualified-baseline provenance, and ``nsx`` is the facade over the
``neuralspotx`` API.
"""
