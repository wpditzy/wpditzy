#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?format=json&lsp__id=121&limit=100&ordering=net"
OUT = Path(__file__).with_name("spacex-launches.ics")

# Temporary official-source corrections used when the upstream launch database
# is behind SpaceX's own launch page. The override only moves an event later;
# once the upstream source catches up (or moves later again), it wins normally.
OFFICIAL_OVERRIDES = {
    "17c71937-dd80-406f-bb47-0c9ee9a24276": {
        "net": "2026-09-17T01:00:00Z",
        "window_start": "2026-09-17T01:00:00Z",
        "window_end": "2026-09-17T05:00:00Z",
        "url": "https://www.spacex.com/launches/ussf259",
        "source_note": "SpaceX 官方页面（北京时间 2026-09-17 09:00–13:00）",
    },
}


def esc(value: str | None) -> str:
    if not value:
        return ""
    return (str(value)
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n"))


def fold(line: str, limit: int = 73) -> str:
    if len(line) <= limit:
        return line
    parts = [line[:limit]]
    line = line[limit:]
    while line:
        parts.append(" " + line[:limit - 1])
        line = line[limit - 1:]
    return "\r\n".join(parts)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_json(url: str, attempts: int = 4) -> dict:
    headers = {
        "User-Agent": "wpditzy-spacex-calendar/1.2",
        "Accept": "application/json",
    }
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except (TimeoutError, socket.timeout, urllib.error.URLError,
                urllib.error.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = 5 * (2 ** (attempt - 1))
            print(f"Fetch attempt {attempt}/{attempts} failed: {exc}; retrying in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"Launch Library request failed after {attempts} attempts: {last_error}")


def apply_official_overrides(launches: list[dict]) -> None:
    for launch in launches:
        override = OFFICIAL_OVERRIDES.get(str(launch.get("id")))
        if not override:
            continue
        current = parse_dt(launch.get("net"))
        official = parse_dt(override["net"])
        # Never move a newer upstream schedule backwards.
        if current is None or (official is not None and current < official):
            launch["net"] = override["net"]
            launch["window_start"] = override["window_start"]
            launch["window_end"] = override["window_end"]
            launch["url"] = override["url"]
            launch["calendar_source_note"] = override["source_note"]
            # The official page currently provides a launch window, not an
            # exact T-0 within that window.
            launch["net_precision"] = {"abbrev": "HR", "name": "Hour"}
            print(f"Applied SpaceX official override to {launch.get('name')}")


def fetch_launches() -> list[dict]:
    url = API
    results: list[dict] = []
    pages = 0
    while url and pages < 5:
        payload = fetch_json(url)
        results.extend(payload.get("results", []))
        url = payload.get("next")
        pages += 1
    launches = [
        x for x in results
        if (x.get("launch_service_provider") or {}).get("id") == 121
        or (x.get("launch_service_provider") or {}).get("name") == "SpaceX"
    ]
    apply_official_overrides(launches)
    return launches


def add_event(lines: list[str], launch: dict, stamp: str) -> None:
    launch_id = launch.get("id") or launch.get("slug") or launch.get("name", "unknown")
    name = launch.get("name") or "SpaceX Launch"
    mission = launch.get("mission") or {}
    rocket = (launch.get("rocket") or {}).get("configuration") or {}
    status = launch.get("status") or {}
    precision = launch.get("net_precision") or {}
    pad = launch.get("pad") or {}
    location = pad.get("location") or {}
    net = parse_dt(launch.get("net"))
    if net is None:
        return

    precision_abbrev = (precision.get("abbrev") or "").upper()
    precision_name = precision.get("name") or ""
    is_precise = precision_abbrev in {"SEC", "MIN", "HR", "HOUR"}
    tentative = status.get("abbrev") in {"TBD", "TBC"} or not is_precise

    summary_name = mission.get("name") or name.split("|", 1)[-1].strip()
    summary = f"🚀 SpaceX｜{summary_name}"
    if tentative:
        summary += "（暂定）"

    desc_parts = [
        f"运载器：{rocket.get('full_name') or rocket.get('name') or '待定'}",
        f"状态：{status.get('name') or '待定'}",
        f"时间精度：{precision_name or '待定'}",
    ]
    if mission.get("description"):
        desc_parts.append(f"任务：{mission['description']}")
    if launch.get("window_start") and launch.get("window_end"):
        desc_parts.append(f"发射窗口（UTC）：{launch['window_start']} – {launch['window_end']}")
    if launch.get("calendar_source_note"):
        desc_parts.append(f"官方校正：{launch['calendar_source_note']}")
    desc_parts.append("数据源：SpaceX 官方 / Launch Library 2 / The Space Devs")
    source_url = launch.get("url") or "https://www.spacex.com/launches/"
    desc_parts.append(f"详情：{source_url}")

    place = " / ".join(filter(None, [pad.get("name"), location.get("name")]))
    lines.extend([
        "BEGIN:VEVENT",
        f"UID:{esc(str(launch_id))}@spacex.wpditzy",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        f"SUMMARY:{esc(summary)}",
        f"LOCATION:{esc(place)}",
        f"DESCRIPTION:{esc(chr(10).join(desc_parts))}",
        f"URL:{esc(source_url)}",
        f"STATUS:{'TENTATIVE' if tentative else 'CONFIRMED'}",
    ])

    if is_precise:
        start = net
        window_end = parse_dt(launch.get("window_end"))
        end = window_end if window_end and window_end > start else start + timedelta(hours=1)
        lines.extend([
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            "BEGIN:VALARM",
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(summary_name)} 将在约 1 天后进入发射窗口",
            "END:VALARM",
        ])
    else:
        start_date = net.date()
        end_date = start_date + timedelta(days=1)
        lines.extend([
            f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end_date.strftime('%Y%m%d')}",
            "BEGIN:VALARM",
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(summary_name)} 当前暂定日期为明天",
            "END:VALARM",
        ])
    lines.append("END:VEVENT")


def main() -> None:
    try:
        launches = fetch_launches()
    except Exception as exc:
        print(f"WARNING: {exc}")
        if OUT.exists() and OUT.stat().st_size > 0:
            print(f"Keeping existing calendar unchanged: {OUT}")
            return
        raise

    if not launches:
        print("WARNING: API returned no SpaceX launches; keeping existing calendar")
        if OUT.exists() and OUT.stat().st_size > 0:
            return
        raise RuntimeError("No SpaceX launches returned and no previous calendar exists")

    launches.sort(key=lambda x: x.get("net") or "")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//wpditzy//SpaceX Launch Calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:SpaceX 发射任务",
        "X-WR-CALDESC:SpaceX 发射任务订阅；自动更新，优先采用 SpaceX 官方最新排期。",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "REFRESH-INTERVAL;VALUE=DURATION:PT2H",
        "X-PUBLISHED-TTL:PT2H",
    ]
    for launch in launches:
        add_event(lines, launch, stamp)
    lines.append("END:VCALENDAR")
    OUT.write_text("\r\n".join(fold(line) for line in lines) + "\r\n", encoding="utf-8")
    print(f"Wrote {len(launches)} SpaceX launches to {OUT}")


if __name__ == "__main__":
    main()
