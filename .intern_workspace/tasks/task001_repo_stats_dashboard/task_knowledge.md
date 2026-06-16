<!-- METADATA:SESSION=0 -->

# Task Knowledge

- Dashboard service lives in `dashboard/server.py` and serves `dashboard/index.html`.
- Existing Feishu sync writes `dashboard/dashboard-data.js` and keeps runtime state in ignored JSON files.
- Repository collection data is available from Aurora PostgreSQL through ECS Exec into the `fetch-gh-data-1` ECS service container.
