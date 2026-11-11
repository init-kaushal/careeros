"""Interview core: the pure contracts behind interview round records and the round state derived from the ledger
(spec docs/superpowers/specs/2026-10-31-interview-rounds-design.md).

Modules: `rules` (constants, enums, event types, refusal and validation codes, the transition table as data), `_common`
(the small record validators), `model` (frozen data shapes), `round` (strict record parsing and bytes), `fold` (round
flags folded from events and the writers of the event shapes) and `derive` (`needs_update`, the two views and the status
and record findings). Everything here is pure: no clock, no filesystem, no ledger writes; every function that needs the
time takes `now`. Nothing here imports `careeros.core.outreach`. State is derived from the ledger and never stored in a
record. This is a recorded decision, not a security boundary.
"""
