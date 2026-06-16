# Code Data Dashboard

This directory contains the Code Data kanban dashboard service.

## Run

```bash
cd dashboard
PORT=8522 python3 server.py
```

The server binds to `0.0.0.0:$PORT` and serves:

- `/` - dashboard UI
- `/dashboard-data.js` - current dashboard snapshot
- `/repo-stats.json` - current repository collection statistics
- `/cephfs-repo-stats.json` - current CephFS repository collection statistics
- `/health` - sync state JSON
- `/events` - server-sent events for live browser reloads

## Feishu Sync

`server.py` reads `sync-rule.json`, fetches the configured Feishu sheet using
`~/.feishu_skill_token.json`, and writes `dashboard-data.js`.

If the Feishu token is unavailable but `dashboard-data.js` exists, the server
still starts and serves the cached dashboard snapshot. The background poller
continues retrying sync and clears `/health.last_error` once the token works.

Runtime files such as logs, PID files, `sync-state.json`, bytecode, and temporary
write files are intentionally ignored by git.

## Repository Stats

`server.py` refreshes repository collection statistics every 12 hours by running
an ECS Exec command against the configured API container and using that
container's `$DATABASE_URL` for PostgreSQL access. The latest successful result
is cached in `repo-stats.json`; the dashboard keeps serving cached stats if a
later refresh fails.

At the same interval, `server.py` refreshes CephFS repository collection
statistics from compacted parquet metadata under
`/mnt/cephfs/data/processing/github_dl_parquet_compacted`. It reads only the
manifest and metadata parquet columns required for status and star buckets,
then caches the latest successful result in `cephfs-repo-stats.json`.

Environment overrides:

- `REPO_STATS_REFRESH_SECONDS`
- `REPO_STATS_RETRY_SECONDS`
- `REPO_STATS_AWS_REGION`
- `REPO_STATS_ECS_CLUSTER`
- `REPO_STATS_ECS_SERVICE`
- `REPO_STATS_ECS_CONTAINER`
- `REPO_STATS_QUERY_TIMEOUT_SECONDS`
- `CEPHFS_REPO_STATS_ROOT`
