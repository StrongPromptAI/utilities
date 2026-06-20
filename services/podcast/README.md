# podcast — StrongPrompt podcast server

Multi-show podcast RSS server. A show is a folder of MP3s on a Railway **volume**
(`/data/audio/<folder>/`) + an editable metadata row in SQLite (`/data/podcast.db`).
No object storage; audio is served straight off the volume with HTTP Range.

- **Private shows** — `GET /{slug}/{code}/feed.xml` + `/{slug}/{code}/ep/{name}.mp3` (code in URL).
- **Public show** — `GET /{slug}/feed.xml` + `/{slug}/ep/{name}.mp3` (no code).
- **Admin** — `/admin` (Starlette-Admin, OIDC-gated) + `/admin/volume` (read-only volume listing).
- **Health** — `GET /health`.

Plan: `symlink_docs/plans/26-6-19-podcast-server.md`. Deploys to the `strong-website`
Railway project at `podcast.strongprompt.ai`. Local port **8103**.

## Run locally

```bash
cd services/podcast
export PODCAST_DB_PATH=./data/podcast.db          # keep the DB out of /data in dev
uv run python seed.py                              # create tables + seed the 4 shows
uv run uvicorn app:app --port 8103 --reload
curl -s localhost:8103/health | jq .status         # "ok"
```

## Storage layout (on the volume / `./data` locally)

```
/data/podcast.db                       SQLite metadata (WAL)
/data/audio/<folder>/<file>.mp3        episode audio
/data/audio/<folder>/<file>.md         transcript sidecar (optional → feed description)
/data/audio/<folder>/_art.png          show cover art (optional → <itunes:image>)
```
