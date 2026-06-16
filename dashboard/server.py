#!/usr/bin/env python3
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Condition, Thread

ROOT = Path(__file__).resolve().parent
RULE_PATH = ROOT / "sync-rule.json"
DATA_PATH = ROOT / "dashboard-data.js"
STATE_PATH = ROOT / "sync-state.json"
REPO_STATS_PATH = ROOT / "repo-stats.json"
CEPHFS_REPO_STATS_PATH = ROOT / "cephfs-repo-stats.json"
TOKEN_PATH = Path.home() / ".feishu_skill_token.json"
KEY_PATH = Path("/work-agents/key.txt")
OPEN_API = "https://open.feishu.cn/open-apis"
REPO_STATS_REFRESH_SECONDS = int(os.environ.get("REPO_STATS_REFRESH_SECONDS", str(12 * 60 * 60)))
REPO_STATS_RETRY_SECONDS = int(os.environ.get("REPO_STATS_RETRY_SECONDS", "900"))
REPO_STATS_AWS_REGION = os.environ.get("REPO_STATS_AWS_REGION", "us-east-1")
REPO_STATS_ECS_CLUSTER = os.environ.get("REPO_STATS_ECS_CLUSTER", "fetch-gh-data-1")
REPO_STATS_ECS_SERVICE = os.environ.get("REPO_STATS_ECS_SERVICE", "api")
REPO_STATS_ECS_CONTAINER = os.environ.get("REPO_STATS_ECS_CONTAINER", "api")
REPO_STATS_QUERY_TIMEOUT_SECONDS = int(os.environ.get("REPO_STATS_QUERY_TIMEOUT_SECONDS", "900"))
REPO_STATS_B64_START = "__REPO_STATS_JSON_B64_START__"
REPO_STATS_B64_END = "__REPO_STATS_JSON_B64_END__"
CEPHFS_REPO_STATS_ROOT = Path(os.environ.get(
    "CEPHFS_REPO_STATS_ROOT",
    "/mnt/cephfs/data/processing/github_dl_parquet_compacted",
))

REPO_STATS_SQL = """
WITH snapshot AS (
  SELECT
    count(*) AS total_repos,
    count(*) FILTER (WHERE status = 'succeeded') AS succeeded_total,
    count(*) FILTER (WHERE status = 'succeeded' AND star_count IS NOT NULL AND star_count < 5) AS succeeded_stars_0_5,
    count(*) FILTER (WHERE status = 'succeeded' AND star_count >= 5 AND star_count < 10) AS succeeded_stars_5_10,
    count(*) FILTER (WHERE status = 'succeeded' AND star_count >= 10) AS succeeded_stars_gte_10,
    count(*) FILTER (WHERE status = 'succeeded' AND star_count IS NULL) AS succeeded_stars_unknown,
    count(*) FILTER (WHERE status IS DISTINCT FROM 'succeeded') AS non_succeeded_total
  FROM repos
),
non_succeeded_statuses AS (
  SELECT
    coalesce(status, 'unknown') AS status,
    count(*) AS repo_count
  FROM repos
  WHERE status IS DISTINCT FROM 'succeeded'
  GROUP BY coalesce(status, 'unknown')
),
payload AS (
  SELECT jsonb_build_object(
    'queriedAt', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'totalRepos', snapshot.total_repos,
    'succeeded', jsonb_build_object(
      'total', snapshot.succeeded_total,
      'starBuckets', jsonb_build_array(
        jsonb_build_object('key', 'stars_0_5', 'label', '[0, 5)', 'count', snapshot.succeeded_stars_0_5),
        jsonb_build_object('key', 'stars_5_10', 'label', '[5, 10)', 'count', snapshot.succeeded_stars_5_10),
        jsonb_build_object('key', 'stars_gte_10', 'label', '>= 10', 'count', snapshot.succeeded_stars_gte_10),
        jsonb_build_object('key', 'stars_unknown', 'label', 'unknown', 'count', snapshot.succeeded_stars_unknown)
      )
    ),
    'nonSucceeded', jsonb_build_object(
      'total', snapshot.non_succeeded_total,
      'statuses', (
        SELECT coalesce(
          jsonb_agg(
            jsonb_build_object('status', status, 'count', repo_count)
            ORDER BY repo_count DESC, status
          ),
          '[]'::jsonb
        )
        FROM non_succeeded_statuses
      )
    )
  )::text AS body
  FROM snapshot
)
SELECT regexp_replace(
  encode(convert_to(body, 'UTF8'), 'base64'),
  '[[:space:]]+',
  '',
  'g'
)
FROM payload;
""".strip()

state = {
    "revision": None,
    "updated_at": None,
    "version": 0,
    "last_error": None,
}
condition = Condition()

repo_stats_state = {
    "updated_at": None,
    "last_attempt_at": None,
    "last_error": None,
    "version": 0,
    "refresh_interval_seconds": REPO_STATS_REFRESH_SECONDS,
}
repo_stats_condition = Condition()

cephfs_repo_stats_state = {
    "updated_at": None,
    "last_attempt_at": None,
    "last_error": None,
    "version": 0,
    "refresh_interval_seconds": REPO_STATS_REFRESH_SECONDS,
}
cephfs_repo_stats_condition = Condition()


def read_cached_metadata():
    if not DATA_PATH.exists():
        return None
    match = re.match(r"window\.DASHBOARD_DATA = (.*);\s*$", DATA_PATH.read_text(), re.S)
    if not match:
        return None
    payload = json.loads(match.group(1))
    return {
        "revision": payload.get("revision"),
        "updated_at": payload.get("generatedAt"),
    }


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(path)


def utc_now_string():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_repo_stats_payload():
    return {
        "queriedAt": None,
        "generatedAt": None,
        "totalRepos": None,
        "succeeded": {
            "total": None,
            "starBuckets": [
                {"key": "stars_0_5", "label": "[0, 5)", "count": None},
                {"key": "stars_5_10", "label": "[5, 10)", "count": None},
                {"key": "stars_gte_10", "label": ">= 10", "count": None},
                {"key": "stars_unknown", "label": "unknown", "count": None},
            ],
        },
        "nonSucceeded": {
            "total": None,
            "statuses": [],
        },
        "refreshIntervalSeconds": REPO_STATS_REFRESH_SECONDS,
        "lastAttemptAt": repo_stats_state["last_attempt_at"],
        "lastError": repo_stats_state["last_error"],
        "loading": True,
    }


def default_star_buckets():
    return [
        {"key": "stars_0_5", "label": "[0, 5)", "count": None},
        {"key": "stars_5_10", "label": "[5, 10)", "count": None},
        {"key": "stars_gte_10", "label": ">= 10", "count": None},
        {"key": "stars_unknown", "label": "unknown", "count": None},
    ]


def empty_cephfs_repo_stats_payload():
    return {
        "queriedAt": None,
        "generatedAt": None,
        "totalRepos": None,
        "starBuckets": default_star_buckets(),
        "archiveStatus": {
            "total": None,
            "statuses": [],
        },
        "metaRows": None,
        "refreshIntervalSeconds": REPO_STATS_REFRESH_SECONDS,
        "lastAttemptAt": cephfs_repo_stats_state["last_attempt_at"],
        "lastError": cephfs_repo_stats_state["last_error"],
        "loading": True,
    }


def read_cached_repo_stats():
    if not REPO_STATS_PATH.exists():
        return None
    payload = read_json(REPO_STATS_PATH)
    payload["refreshIntervalSeconds"] = REPO_STATS_REFRESH_SECONDS
    payload["lastAttemptAt"] = repo_stats_state["last_attempt_at"]
    payload["lastError"] = repo_stats_state["last_error"]
    payload["loading"] = False
    return payload


def read_cached_cephfs_repo_stats():
    if not CEPHFS_REPO_STATS_PATH.exists():
        return None
    payload = read_json(CEPHFS_REPO_STATS_PATH)
    payload["refreshIntervalSeconds"] = REPO_STATS_REFRESH_SECONDS
    payload["lastAttemptAt"] = cephfs_repo_stats_state["last_attempt_at"]
    payload["lastError"] = cephfs_repo_stats_state["last_error"]
    payload["loading"] = False
    return payload


def update_repo_stats_state(**changes):
    with repo_stats_condition:
        repo_stats_state.update(changes)
        repo_stats_state["version"] += 1
        repo_stats_condition.notify_all()


def update_cephfs_repo_stats_state(**changes):
    with cephfs_repo_stats_condition:
        cephfs_repo_stats_state.update(changes)
        cephfs_repo_stats_state["version"] += 1
        cephfs_repo_stats_condition.notify_all()


def run_checked(args, timeout, combine_output=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args[:4])}; {detail[:1200]}")
    if combine_output:
        return "\n".join(part for part in (result.stdout, result.stderr) if part)
    return result.stdout


def current_ecs_task_arn():
    output = run_checked([
        "aws", "ecs", "list-tasks",
        "--region", REPO_STATS_AWS_REGION,
        "--cluster", REPO_STATS_ECS_CLUSTER,
        "--service-name", REPO_STATS_ECS_SERVICE,
        "--desired-status", "RUNNING",
        "--query", "taskArns[0]",
        "--output", "text",
    ], timeout=45).strip()
    if not output or output == "None":
        raise RuntimeError(
            f"no running ECS task found for {REPO_STATS_ECS_CLUSTER}/{REPO_STATS_ECS_SERVICE}"
        )
    return output


def extract_json_payload(text):
    marker = re.search(
        rf"{REPO_STATS_B64_START}\s*([A-Za-z0-9+/=\s]+?)\s*{REPO_STATS_B64_END}",
        text,
        re.S,
    )
    if marker:
        encoded = re.sub(r"\s+", "", marker.group(1))
        decoded = base64.b64decode(encoded).decode()
        return json.loads(decoded)

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
            return payload
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"repo stats query did not return JSON: {text[:1200]}")


def ecs_psql_json(sql):
    encoded_sql = base64.b64encode(sql.encode()).decode()
    script = (
        f"set -e; SQL=$(printf %s {encoded_sql} | base64 -d); "
        'payload=$(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X -q -t -A -c "$SQL"); '
        f"printf '\\n{REPO_STATS_B64_START}\\n%s\\n{REPO_STATS_B64_END}\\n' \"$payload\""
    )
    output = run_checked([
        "aws", "ecs", "execute-command",
        "--region", REPO_STATS_AWS_REGION,
        "--cluster", REPO_STATS_ECS_CLUSTER,
        "--task", current_ecs_task_arn(),
        "--container", REPO_STATS_ECS_CONTAINER,
        "--interactive",
        "--command", f"bash -lc {shlex.quote(script)}",
    ], timeout=REPO_STATS_QUERY_TIMEOUT_SECONDS, combine_output=True)
    return extract_json_payload(output)


def refresh_repo_stats():
    attempted_at = utc_now_string()
    update_repo_stats_state(last_attempt_at=attempted_at)
    payload = ecs_psql_json(REPO_STATS_SQL)
    payload["generatedAt"] = attempted_at
    payload["refreshIntervalSeconds"] = REPO_STATS_REFRESH_SECONDS
    payload["source"] = {
        "awsRegion": REPO_STATS_AWS_REGION,
        "ecsCluster": REPO_STATS_ECS_CLUSTER,
        "ecsService": REPO_STATS_ECS_SERVICE,
        "ecsContainer": REPO_STATS_ECS_CONTAINER,
        "database": "fetch_db",
    }
    write_json(REPO_STATS_PATH, payload)
    update_repo_stats_state(
        updated_at=payload.get("queriedAt") or attempted_at,
        last_error=None,
    )
    return payload


def parquet_files_in(path):
    if not path.exists():
        raise RuntimeError(f"missing parquet directory: {path}")
    files = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".parquet"):
                files.append(entry.path)
    if not files:
        raise RuntimeError(f"no parquet files found in {path}")
    return files


def add_bucket_counts(counts, stars):
    import pyarrow.compute as pc

    valid = pc.is_valid(stars)
    counts["total"] += len(stars)
    counts["stars_unknown"] += pc.sum(pc.invert(valid)).as_py() or 0
    counts["stars_0_5"] += pc.sum(pc.and_(valid, pc.less(stars, 5))).as_py() or 0
    counts["stars_5_10"] += pc.sum(pc.and_(pc.greater_equal(stars, 5), pc.less(stars, 10))).as_py() or 0
    counts["stars_gte_10"] += pc.sum(pc.and_(valid, pc.greater_equal(stars, 10))).as_py() or 0


def scan_star_buckets(dataset, row_filter=None):
    counts = Counter({
        "total": 0,
        "stars_0_5": 0,
        "stars_5_10": 0,
        "stars_gte_10": 0,
        "stars_unknown": 0,
    })
    for batch in dataset.to_batches(columns=["stargazer_count"], filter=row_filter, batch_size=262144):
        add_bucket_counts(counts, batch.column(0))
    return counts


def star_buckets_from_counts(counts):
    return [
        {"key": "stars_0_5", "label": "[0, 5)", "count": int(counts["stars_0_5"])},
        {"key": "stars_5_10", "label": "[5, 10)", "count": int(counts["stars_5_10"])},
        {"key": "stars_gte_10", "label": ">= 10", "count": int(counts["stars_gte_10"])},
        {"key": "stars_unknown", "label": "unknown", "count": int(counts["stars_unknown"])},
    ]


def collect_cephfs_repo_stats():
    import pyarrow.dataset as ds

    meta_path = CEPHFS_REPO_STATS_ROOT / "meta"
    manifest_path = CEPHFS_REPO_STATS_ROOT / "manifest"
    meta_files = parquet_files_in(meta_path)
    manifest_files = parquet_files_in(manifest_path)

    manifest = ds.dataset(manifest_files, format="parquet")
    status_counts = Counter()
    status_meta_rows = Counter()
    error_with_meta = []
    manifest_rows = 0
    meta_row_count_sum = 0

    for batch in manifest.to_batches(columns=["repo_full_name", "status", "meta_row_count"], batch_size=262144):
        manifest_rows += batch.num_rows
        names = batch.column(0).to_pylist()
        statuses = batch.column(1).to_pylist()
        meta_counts = batch.column(2).to_pylist()
        for name, status, meta_count in zip(names, statuses, meta_counts):
            status_key = status or "unknown"
            row_meta_count = int(meta_count or 0)
            status_counts[status_key] += 1
            status_meta_rows[status_key] += row_meta_count
            meta_row_count_sum += row_meta_count
            if status_key != "ok" and row_meta_count > 0 and name:
                error_with_meta.append(name)

    meta = ds.dataset(meta_files, format="parquet")
    all_star_counts = scan_star_buckets(meta)
    excluded_star_counts = Counter({
        "total": 0,
        "stars_0_5": 0,
        "stars_5_10": 0,
        "stars_gte_10": 0,
        "stars_unknown": 0,
    })
    name_column = None
    for candidate in ("canonical_full_name", "full_name", "repo_full_name"):
        if candidate in meta.schema.names:
            name_column = candidate
            break
    if error_with_meta and name_column:
        excluded_filter = ds.field(name_column).isin(error_with_meta)
        excluded_star_counts = scan_star_buckets(meta, row_filter=excluded_filter)

    ok_star_counts = Counter()
    for key in ("total", "stars_0_5", "stars_5_10", "stars_gte_10", "stars_unknown"):
        ok_star_counts[key] = int(all_star_counts[key] - excluded_star_counts[key])

    statuses = [
        {
            "status": status,
            "count": int(count),
            "metaRows": int(status_meta_rows[status]),
        }
        for status, count in status_counts.items()
    ]
    statuses.sort(key=lambda item: (-item["count"], item["status"]))

    return {
        "queriedAt": utc_now_string(),
        "totalRepos": int(ok_star_counts["total"]),
        "starBuckets": star_buckets_from_counts(ok_star_counts),
        "archiveStatus": {
            "total": int(manifest_rows),
            "statuses": statuses,
        },
        "metaRows": int(all_star_counts["total"]),
        "excludedMetaRows": int(excluded_star_counts["total"]),
        "manifestMetaRows": int(meta_row_count_sum),
        "source": {
            "root": str(CEPHFS_REPO_STATS_ROOT),
            "metaPath": str(meta_path),
            "manifestPath": str(manifest_path),
            "metaFiles": len(meta_files),
            "manifestFiles": len(manifest_files),
        },
    }


def refresh_cephfs_repo_stats():
    attempted_at = utc_now_string()
    update_cephfs_repo_stats_state(last_attempt_at=attempted_at)
    payload = collect_cephfs_repo_stats()
    payload["generatedAt"] = attempted_at
    payload["refreshIntervalSeconds"] = REPO_STATS_REFRESH_SECONDS
    write_json(CEPHFS_REPO_STATS_PATH, payload)
    update_cephfs_repo_stats_state(
        updated_at=payload.get("queriedAt") or attempted_at,
        last_error=None,
    )
    return payload


def repo_stats_loop():
    cached = read_cached_repo_stats()
    if cached:
        update_repo_stats_state(updated_at=cached.get("queriedAt") or cached.get("generatedAt"))
    cephfs_cached = read_cached_cephfs_repo_stats()
    if cephfs_cached:
        update_cephfs_repo_stats_state(
            updated_at=cephfs_cached.get("queriedAt") or cephfs_cached.get("generatedAt")
        )
    while True:
        started = time.monotonic()
        failed = False
        try:
            payload = refresh_repo_stats()
            print(f"[repo-stats] updated_at={payload.get('queriedAt')} total={payload.get('totalRepos')}", flush=True)
        except Exception as exc:
            failed = True
            update_repo_stats_state(last_error=str(exc))
            print(f"[repo-stats:error] {exc}", file=sys.stderr, flush=True)
        try:
            payload = refresh_cephfs_repo_stats()
            print(
                f"[cephfs-repo-stats] updated_at={payload.get('queriedAt')} total={payload.get('totalRepos')}",
                flush=True,
            )
        except Exception as exc:
            failed = True
            update_cephfs_repo_stats_state(last_error=str(exc))
            print(f"[cephfs-repo-stats:error] {exc}", file=sys.stderr, flush=True)
        delay = min(REPO_STATS_RETRY_SECONDS, REPO_STATS_REFRESH_SECONDS) if failed else REPO_STATS_REFRESH_SECONDS
        elapsed = time.monotonic() - started
        time.sleep(max(60, int(delay - elapsed)))


def request_json(url, method="GET", token=None, body=None):
    headers = {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:1200]}") from exc


def refresh_user_token(token_data):
    if not KEY_PATH.exists():
        raise RuntimeError(f"missing app credentials: {KEY_PATH}")
    app_id, app_secret = KEY_PATH.read_text().splitlines()[:2]
    body = {
        "grant_type": "refresh_token",
        "client_id": app_id.strip(),
        "client_secret": app_secret.strip(),
        "refresh_token": token_data["refresh_token"],
    }
    result = request_json(f"{OPEN_API}/authen/v2/oauth/token", method="POST", body=body)
    if result.get("code") != 0:
        raise RuntimeError(f"token refresh failed: {result}")
    token_data.update({k: result[k] for k in ("access_token", "refresh_token", "scope") if k in result})
    if "expires_in" in result:
        token_data["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(result["expires_in"]))).isoformat()
    if "refresh_token_expires_in" in result:
        token_data["refresh_expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=int(result["refresh_token_expires_in"]))
        ).isoformat()
    write_json(TOKEN_PATH, token_data)
    TOKEN_PATH.chmod(0o600)
    return token_data["access_token"]


def get_user_token():
    token_data = read_json(TOKEN_PATH)
    expires_at = token_data.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < expiry - timedelta(minutes=5):
                return token_data["access_token"]
        except ValueError:
            pass
    return refresh_user_token(token_data)


def cell_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        if value.get("type") == "mention" or value.get("category") == "at-user-block":
            return value.get("name") or value.get("en_name") or value.get("text", "").lstrip("@")
        return value.get("text") or value.get("name") or value.get("link") or json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "".join(cell_text(item) for item in value)
    return str(value)


def clean_task_name(value):
    text = cell_text(value)
    text = text.replace("[File-level / repo-level FIM](", "File-level / repo-level FIM")
    text = text.replace(")", "")
    text = re.sub(r"https?://\S+", "", text).strip()
    return text or "Untitled task"


def parse_date(value):
    text = cell_text(value).strip()
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return date(1899, 12, 30) + timedelta(days=int(float(text)))
    year, month, day = map(int, text.split("-"))
    return date(year, month, day)


def normalize(text):
    return re.sub(r"\s+", "", text.lower())


def transform_sheet(sheet_data, revision):
    values = sheet_data["data"]["valueRange"]["values"]
    headers = (values[4] + [""] * 22)[:22]
    week_headers = headers[7:22]
    tasks = []
    for index, row in enumerate(values[5:41], start=1):
        row = (row + [""] * 22)[:22]
        if not cell_text(row[1]).strip():
            continue
        start_raw = row[4]
        end_raw = row[5]
        if not cell_text(start_raw).strip() or not cell_text(end_raw).strip():
            continue
        d0 = parse_date(start_raw)
        d1 = parse_date(end_raw)
        tasks.append({
            "id": f"T{index:02d}",
            "row": index + 5,
            "stage": cell_text(row[0]).strip() or "Unstaged",
            "task": clean_task_name(row[1]),
            "owner": cell_text(row[2]).strip() or "TBD",
            "status": cell_text(row[3]).strip() or "待办",
            "start": d0.isoformat(),
            "end": d1.isoformat(),
            "notes": cell_text(row[6]).strip(),
            "weeks": [cell_text(item).strip() for item in row[7:22]],
            "durationDays": (d1 - d0).days + 1,
        })

    manual_aliases = {
        "Github 下载": ["Github 代码下载与汇总", "Github 下载进度与质量确认"],
        "Github 下载完成": ["Github 代码下载与汇总"],
        "高质量仓库筛选": ["高质量仓库优先筛选 >=5 stars"],
        "动态开发数据": ["Natural diff patch 数据", "Commit packs 数据", "更多开发轨迹数据"],
        "PR 筛选调研": ["PR 筛选方法调研"],
        "OCR": ["Literature OCR pipeline"],
        "PDF OCR code snippets": ["PDF / literature code snippets 召回"],
        "PDF OCR": ["Literature OCR pipeline"],
        "CC 召回": ["Common Crawl programming pages 召回"],
        "CC programming pages": ["Common Crawl programming pages 召回"],
        "打分模型": ["质量打分模型 pipeline"],
        "agentic 结果": ["Execution-based agentic trajectory"],
        "pretrain FIM": ["File-level / repo-level FIM"],
        "pretrain trajectories": ["Contextually-native trajectories"],
        "Synthetic Single-turn QA": ["Synthetic Single-turn QA"],
        "ipynb": ["从 Github 拉回 ipynb"],
        "Kaggle 数据源": ["Kaggle Notebook 数据源确认"],
        "Github 数据确认": ["Github 下载进度与质量确认"],
    }
    name_to_id = {task["task"]: task["id"] for task in tasks}
    for task in tasks:
        dependencies = []
        note = normalize(task["notes"])
        for other in tasks:
            if other["id"] == task["id"]:
                continue
            if normalize(other["task"]) in note:
                dependencies.append(other["id"])
        for alias, names in manual_aliases.items():
            if normalize(alias) in note:
                dependencies.extend(name_to_id[name] for name in names if name_to_id.get(name) and name_to_id[name] != task["id"])
        seen = set()
        task["dependencies"] = [dep for dep in dependencies if not (dep in seen or seen.add(dep))]

    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sourceUrl": f"https://acnn1zogjo15.feishu.cn/sheets/{read_json(RULE_PATH)['spreadsheet_token']}",
        "revision": revision,
        "weekHeaders": week_headers,
        "tasks": tasks,
    }


def fetch_sheet(rule):
    token = get_user_token()
    sheet_range = f"{rule['sheet_id']}!{rule['read_range']}"
    encoded = urllib.parse.quote(sheet_range, safe="!:")
    return request_json(f"{OPEN_API}/sheets/v2/spreadsheets/{rule['spreadsheet_token']}/values/{encoded}", token=token)


def regenerate_if_needed(force=False):
    rule = read_json(RULE_PATH)
    sheet_data = fetch_sheet(rule)
    if sheet_data.get("code") != 0:
        raise RuntimeError(f"sheet read failed: {sheet_data}")
    revision = sheet_data.get("data", {}).get("revision")
    if force or revision != state["revision"] or not DATA_PATH.exists():
        payload = transform_sheet(sheet_data, revision)
        tmp = DATA_PATH.with_suffix(".js.tmp")
        tmp.write_text("window.DASHBOARD_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n")
        tmp.replace(DATA_PATH)
        write_json(STATE_PATH, {
            "revision": revision,
            "updated_at": payload["generatedAt"],
            "task_count": len(payload["tasks"]),
            "dependency_count": sum(len(task["dependencies"]) for task in payload["tasks"]),
            "rule": rule,
        })
        with condition:
            state["revision"] = revision
            state["updated_at"] = payload["generatedAt"]
            state["last_error"] = None
            state["version"] += 1
            condition.notify_all()
        return True
    return False


def poll_loop():
    while True:
        rule = read_json(RULE_PATH)
        try:
            changed = regenerate_if_needed()
            if changed:
                print(f"[sync] revision={state['revision']} updated_at={state['updated_at']}", flush=True)
        except Exception as exc:
            with condition:
                state["last_error"] = str(exc)
                condition.notify_all()
            print(f"[sync:error] {exc}", file=sys.stderr, flush=True)
        time.sleep(max(5, int(rule.get("poll_interval_seconds", 30))))


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            payload = dict(state)
            payload["repo_stats"] = dict(repo_stats_state)
            payload["cephfs_repo_stats"] = dict(cephfs_repo_stats_state)
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())
            return
        if path == "/repo-stats.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            payload = read_cached_repo_stats() or empty_repo_stats_payload()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())
            return
        if path == "/cephfs-repo-stats.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            payload = read_cached_cephfs_repo_stats() or empty_cephfs_repo_stats_payload()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())
            return
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            current = dict(state)
            last_version = current["version"]
            try:
                self.wfile.write(
                    f"event: sheet-update\ndata: {json.dumps(current, ensure_ascii=False)}\n\n".encode()
                )
                self.wfile.flush()
                while True:
                    with condition:
                        condition.wait(timeout=20)
                        current = dict(state)
                    if current["version"] != last_version:
                        last_version = current["version"]
                        payload = json.dumps(current, ensure_ascii=False)
                        self.wfile.write(f"event: sheet-update\ndata: {payload}\n\n".encode())
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        return super().do_GET()


def main():
    os.chdir(ROOT)
    port = int(os.environ.get("PORT", "8522"))
    try:
        regenerate_if_needed(force=True)
    except Exception as exc:
        cached = read_cached_metadata()
        if not cached:
            raise
        with condition:
            state.update(cached)
            state["last_error"] = str(exc)
            state["version"] += 1
        print(f"[sync:error] initial refresh failed; serving cached dashboard data: {exc}", file=sys.stderr, flush=True)
    Thread(target=poll_loop, daemon=True).start()
    Thread(target=repo_stats_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[server] http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
