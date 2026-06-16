<!-- METADATA:SESSION=1 -->

# Task Knowledge

- Dashboard service lives in `dashboard/server.py` and serves `dashboard/index.html`.
- Existing Feishu sync writes `dashboard/dashboard-data.js` and keeps runtime state in ignored JSON files.
- Repository collection data is available from Aurora PostgreSQL through ECS Exec into the `fetch-gh-data-1` ECS service container.
- New stats endpoint is `/repo-stats.json`; latest successful payload is cached in ignored `dashboard/repo-stats.json`.
- Verified at 2026-06-16T07:37:28Z: total repos 44,284,582; succeeded 33,644,464; failed 10,640,118.
- Dashboard service runs from tmux session `code_data_kanban_dashboard` on `0.0.0.0:8522`.
