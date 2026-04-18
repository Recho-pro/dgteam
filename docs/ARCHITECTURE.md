# DGTEAM Architecture

## Core split

- Local Agent
  - crawl
  - OCR
  - clean
  - market engine
  - release bundle build
- Cloud SaaS
  - publish API
  - release manager
  - read-only query API
  - web frontend
- Integration layer
  - WeChat clawbot bridge
  - event intake
  - command routing
  - future operator notifications

## Release model

Each publish operation creates:

- `dgteam.db`
- `manifest.json`
- `release.json`
- `market_v1_snapshot.csv`
- `market_v1_clusters.csv`
- `summary.json`

Cloud release switching should only move symlinks or version pointers:

- `current`
- `previous`
- `history/<release_id>`

## Migration rule

Historical `ylt` logic is kept only as migration context in docs. Runtime code, configs, release bundles, and service entrypoints live under `dgteam`.
