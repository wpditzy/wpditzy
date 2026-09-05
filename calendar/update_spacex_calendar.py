#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?format=json&lsp__id=121&limit=100&ordering=net"
OUT = Path(__file__).with_name("spacex-launches.ics")


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
    # iCalendar requires folded content lines. Fold by characters to keep the
    # implementation dependency-free; UTF-8-aware calendar clients accept this.
    if len(line) <= limit:
        return line
    parts = [line[:limit]]
    line = line[limit:]
    while line:
        parts.append(" " + line[:limit - 1])
        line = line[limit - 1:]
    return "\r\n".join(parts)


def fetch_launches() -> list[dict]:
    url = API
    results: list[dict] = []
    headers = {"User-Agent": "wpditzy-spacex-calendar/1.0"}
    while url:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.load(response)
        results.extend(payload.get("results", []))
        url = payload.get("next")
    # Defensive filter: keep only launches whose launch provider is SpaceX.
    return [
        x for x in results
        if (x.get("launch_service_provider") or {}).get("id") == 121
        or (x.get("launch_service_provider") or {}).get("name") == "SpaceX"
    ]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


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
    desc_parts.append(f"数据源：Launch Library 2 / The Space Devs")
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
        end = start + timedelta(hours=1)
        lines.extend([
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            "BEGIN:VALARM",
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(summary_name)} 将在约 1 天后发射",
            "END:VALARM",
        ])
    else:
        # For uncertain day/month/year precision, avoid presenting midnight as
        # an exact launch time. Show it as an all-day tentative event instead.
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
    launches = fetch_launches()
    launches.sort(key=lambda x: x.get("net") or "")
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//wpditzy//SpaceX Launch Calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:SpaceX 发射任务",
        "X-WR-CALDESC:SpaceX 发射任务订阅；自动更新，设备将按本地时区显示。",
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
