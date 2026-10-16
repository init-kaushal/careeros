"""Approvals core: the pure contracts behind recorded, content-bound, single-use approvals of outward messages and
application submissions (spec docs/superpowers/specs/2026-10-15-approvals-design.md).

Modules: `rules` (constants and the transition table), `model` (data shapes), `item` (the item file, its parsing and
action hash), `manifest` (manifest path rules and file hashing under a given root), `render` (terminal-safe display) and
`fold` (state folded from ledger events). Everything except `manifest` is pure: no clock, no filesystem. This is a
recorded decision, not a security boundary.
"""
