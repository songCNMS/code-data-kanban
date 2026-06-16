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
