#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Condition, Thread

ROOT = Path(__file__).resolve().parent
RULE_PATH = ROOT / "sync-rule.json"
DATA_PATH = ROOT / "dashboard-data.js"
STATE_PATH = ROOT / "sync-state.json"
TOKEN_PATH = Path.home() / ".feishu_skill_token.json"
KEY_PATH = Path("/work-agents/key.txt")
OPEN_API = "https://open.feishu.cn/open-apis"

state = {
    "revision": None,
    "updated_at": None,
    "version": 0,
    "last_error": None,
}
condition = Condition()


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
    year, month, day = map(int, value.split("-"))
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
        start = cell_text(row[4]).strip()
        end = cell_text(row[5]).strip()
        if not start or not end:
            continue
        d0 = parse_date(start)
        d1 = parse_date(end)
        tasks.append({
            "id": f"T{index:02d}",
            "row": index + 5,
            "stage": cell_text(row[0]).strip() or "Unstaged",
            "task": clean_task_name(row[1]),
            "owner": cell_text(row[2]).strip() or "TBD",
            "status": cell_text(row[3]).strip() or "待办",
            "start": start,
            "end": end,
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
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(state, ensure_ascii=False).encode())
            return
        if self.path == "/events":
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
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[server] http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
