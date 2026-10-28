"""Outreach core: the pure contracts behind person and target-company records and the state derived from the ledger
(spec docs/superpowers/specs/2026-10-19-outreach-state-design.md).

Modules: `rules` (constants, event types, enums, refusal and validation codes, transition tables as data), `slug`
(`slugify`), `model` (frozen data shapes), `person` and `target` (strict record parsing and bytes), `fold` (person flags,
target decisions and displayed status folded from events, with the plain-data job and record inputs). Everything here is
pure: no clock, no filesystem; every function that needs the time takes `now`. State is derived from the ledger and never
stored in a record. This is a recorded decision, not a security boundary.
"""
