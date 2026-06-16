# intern_code_data_debug - 个人知识库

<!-- METADATA:SESSION=1 -->

---

## 知识条目

- `task001_repo_stats_dashboard`: repo stats are served by `dashboard/server.py` at `/repo-stats.json`, refreshed every 12 hours through ECS Exec into the `fetch-gh-data-1` API container using `$DATABASE_URL`, and cached in ignored `dashboard/repo-stats.json`.
