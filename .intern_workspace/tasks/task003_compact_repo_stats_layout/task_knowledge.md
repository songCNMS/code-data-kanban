<!-- METADATA:SESSION=0 -->

# Task Knowledge

- Dashboard UI lives in `dashboard/index.html`.
- Repository stats are currently rendered by `renderRepoStats` and `renderCephfsRepoStats`.
- The two stats sections are inside the sticky `header`, which makes them occupy page space while scrolling.
- Layout fix keeps `header` sticky for top controls only; `repo-stats-area` now sits below header as a normal, scrollable page section.
