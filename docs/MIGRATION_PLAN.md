# Migration Notes

## Goal

Retire the old `ylt` naming and move the system into the new `dgteam` architecture.

## Completed order

1. Port shared storage and config.
2. Port crawler orchestration.
3. Port OCR cache and image parsing.
4. Port rules and cleaning logic.
5. Port market engine and live publish logic.
6. Port query API ranking and detail assembly.
7. Rebuild the frontend on top of the new query API.
8. Replace manual sync with release upload and rollback flow.
9. Wire WeChat clawbot through the integration layer instead of letting it call core modules directly.

## Rename policy

- `ylt_system` -> `dgteam`
- `ylt_core` -> `dgteam.core` and domain packages
- all new docs, scripts, and configs use `dgteam`
