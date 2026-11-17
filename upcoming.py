#!/usr/bin/env python3
"""Build an "upcoming releases" calendar from your Simkl watchlist.

Reads your Simkl Plan to Watch (movies, shows, anime) and Watching (shows, anime) lists,
looks each title up in Simkl's public calendar files, and writes to ./site:

  index.html     readable list, soonest first, TBC at the bottom
  calendar.ics   subscribe in Google/Apple Calendar
  manifest.json  Stremio-style addon with two catalogues ("Upcoming" for series and movies);
  catalog/...    Nuvio can install it under Settings > Addons

A title with no date in the calendar window is shown as TBC (to be confirmed).
Standard library only. Needs SIMKL_CLIENT_ID and SIMKL_TOKEN (same as lb2simkl.py).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape

APP = "simkl-upcoming"
VERSION = "1.0"
API = "https://api.simkl.com"
CDN = "https://data.simkl.in/calendar/v2"
TZ_NAME = os.environ.get("CAL_TZ", "Asia/Singapore")
MONTHS_AHEAD = 3  # Simkl publishes the current month plus three


def tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:
        return timezone(timedelta(hours=8))


def get_json(url, headers=None):
    h = {"User-Agent": f"{APP}/{VERSION}"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def with_params(url, client_id):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}client_id={client_id}&app-name={APP}&app-version={VERSION}"


# ---------- Simkl: your lists ----------

def fetch_watchlist(client_id, token):
    """List of {simkl, imdb, tmdb, title, year, kind, status, poster}.

    kind is 'movie' or 'series' (anime films count as movies).
    """
    auth = {"Authorization": f"Bearer {token}"}
    wanted = [("movies", "plantowatch"), ("shows", "plantowatch"), ("shows", "watching"),
              ("anime", "plantowatch"), ("anime", "watching")]
    out, seen = [], set()
    for typ, status in wanted:
        data = get_json(with_params(f"{API}/sync/all-items/{typ}/{status}", client_id), auth) or {}
        for it in data.get(typ) or []:
            obj = it.get("movie") or it.get("show") or {}
            ids = obj.get("ids") or {}
            sid = ids.get("simkl")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            is_movie = typ == "movies" or it.get("anime_type") == "movie"
            out.append({"simkl": sid, "imdb": ids.get("imdb"), "tmdb": ids.get("tmdb"),
                        "title": obj.get("title") or "?", "year": obj.get("year"),
                        "kind": "movie" if is_movie else "series", "status": status,
                        "poster": obj.get("poster")})
    return out


# ---------- Simkl: public calendar ----------

def month_list(today, ahead=MONTHS_AHEAD):
    y, m = today.year, today.month
    for _ in range(ahead + 1):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_calendar(client_id, today):
    """Merge monthly calendar files into one {'calendar': [...], 'metadata': {...}} per type."""
    merged = {}
    for typ in ("tv", "anime", "movie_release"):
        cal, meta = [], {}
        for y, m in month_list(today):
            data = get_json(with_params(f"{CDN}/{y}/{m}/{typ}.json", client_id))
            if not data:
                continue
            cal += data.get("calendar") or []
            meta.update(data.get("metadata") or {})
        merged[typ] = {"calendar": cal, "metadata": meta}
    return merged


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_dvd(s):
    """dvd_date arrives as MM/DD/YYYY."""
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None


# ---------- Join ----------

def ep_label(ep):
    if not ep:
        return None
    season, num = ep.get("season"), ep.get("episode")
    if num is None:
        return None
    return f"S{season}E{num}" if season is not None else f"E{num}"


def build_entries(watchlist, cal, today):
    """Return (dated, tbc). dated rows are sorted by when. Each row is a dict."""
    by_id = {w["simkl"]: w for w in watchlist}
    rows = []
    seen_movie_dates = set()
    for typ in ("tv", "anime", "movie_release"):
        meta = cal[typ]["metadata"]
        for item in cal[typ]["calendar"]:
            sid = item.get("simkl_id")
            w = by_id.get(sid)
            if not w:
                continue
            when = parse_dt(item["date"])
            if when.astimezone(tz()).date() < today:
                continue
            ep = item.get("episode")
            m = meta.get(str(sid)) or {}
            if typ == "movie_release" or not ep:
                key = ("rel", sid)
                if key not in seen_movie_dates:
                    seen_movie_dates.add(key)
                    rows.append({"w": w, "when": when, "allday": True, "what": "Release",
                                 "ep": None, "finale": None})
                dvd = parse_dvd(m.get("dvd_date"))
                if dvd and dvd >= today:
                    rows.append({"w": w, "when": datetime(dvd.year, dvd.month, dvd.day,
                                                          tzinfo=timezone.utc),
                                 "allday": True, "what": "Home release", "ep": None,
                                 "finale": None})
            else:
                rows.append({"w": w, "when": when, "allday": False,
                             "what": ep_label(ep) or "Episode", "ep": ep,
                             "finale": item.get("finale_type")})
    rows.sort(key=lambda r: r["when"])
    dated_ids = {r["w"]["simkl"] for r in rows}
    tbc = sorted((w for w in watchlist if w["simkl"] not in dated_ids), key=lambda w: w["title"].lower())
    return rows, tbc


def next_by_title(rows):
    """First upcoming row per title (rows are already sorted)."""
    nxt = {}
    for r in rows:
        nxt.setdefault(r["w"]["simkl"], r)
    return nxt


def when_text(r):
    local = r["when"].astimezone(tz()) if not r["allday"] else r["when"]
    return local.strftime("%a %d %b") + ("" if r["allday"] else local.strftime(" %H:%M"))


# ---------- Output: ICS ----------

def ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    out, b = [], line.encode()
    while len(b) > 74:
        cut = 74
        while cut > 0 and (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode())
        b = b[cut:]
        b = b" " + b
    out.append(b.decode())
    return "\r\n".join(out)


def make_ics(rows, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//simkl-upcoming//EN",
             "X-WR-CALNAME:Simkl watchlist releases", "CALSCALE:GREGORIAN"]
    for r in rows:
        w = r["w"]
        ep = r["ep"] or {}
        summary = f"{w['title']} {r['what']}" if r["ep"] else f"{w['title']} ({r['what'].lower()})"
        if ep.get("title"):
            summary += f" - {ep['title']}"
        if r["finale"]:
            summary += " [finale]"
        uid = f"{w['simkl']}-{r['what']}-{r['when'].strftime('%Y%m%d%H%M')}@simkl-upcoming"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
        if r["allday"]:
            d = r["when"].date()
            lines += [f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                      f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}"]
        else:
            mins = ep.get("runtime") or 30
            end = r["when"] + timedelta(minutes=mins)
            lines += [f"DTSTART:{r['when'].strftime('%Y%m%dT%H%M%SZ')}",
                      f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}"]
        lines += [f"SUMMARY:{ics_escape(summary)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


# ---------- Output: HTML ----------

def make_html(rows, tbc, now=None, today=None):
    import calendar as cal_mod
    now = now or datetime.now(timezone.utc)
    today = today or datetime.now(tz()).date()
    by_day = {}
    for r in rows:
        d = r["when"].date() if r["allday"] else r["when"].astimezone(tz()).date()
        by_day.setdefault(d, []).append(r)
    last = max(by_day) if by_day else today
    months, y, m = [], today.year, today.month
    while (y, m) <= (last.year, last.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    out = []
    for y, m in months or [(today.year, today.month)]:
        out.append(f"<h2>{cal_mod.month_name[m]} {y}</h2><table class=cal><tr>"
                   + "".join(f"<th>{d}</th>" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
                   + "</tr>")
        for week in cal_mod.Calendar(0).monthdayscalendar(y, m):
            out.append("<tr>")
            for day in week:
                if not day:
                    out.append("<td class=empty></td>")
                    continue
                d = date(y, m, day)
                cls = " class=today" if d == today else ""
                evs = []
                for r in by_day.get(d, []):
                    ep = r["ep"] or {}
                    tip = escape(ep.get("title") or "")
                    fin = " (finale)" if r["finale"] else ""
                    evs.append(f"<div class=ev title='{tip}'>{escape(r['w']['title'])}"
                               f" <small>{escape(r['what'])}{fin}</small></div>")
                out.append(f"<td{cls}><b>{day}</b>{''.join(evs)}</td>")
            out.append("</tr>")
        out.append("</table>")
    if tbc:
        out.append("<h2>TBC</h2><ul>" + "".join(f"<li>{escape(w['title'])}</li>" for w in tbc) + "</ul>")
    stamp = now.astimezone(tz()).strftime("%d %b %Y %H:%M")
    css = ("body{font:15px system-ui;margin:1rem;max-width:70rem}table.cal{border-collapse:collapse;width:100%;"
           "table-layout:fixed}.cal th{text-align:left;padding:.3rem;border-bottom:1px solid #999}"
           ".cal td{vertical-align:top;height:5.5rem;padding:.3rem;border:1px solid #ddd;overflow:hidden}"
           ".cal td.empty{background:#f6f6f6}.cal td.today{outline:2px solid #2d7ff9}"
           ".ev{margin-top:.2rem;padding:.1rem .3rem;background:#e6f1fb;border-radius:4px;font-size:12px}"
           ".ev small{color:#444}@media(prefers-color-scheme:dark){body{background:#111;color:#eee}"
           ".cal td{border-color:#333}.cal td.empty{background:#1a1a1a}.ev{background:#1f3a5a}.ev small{color:#bbb}}")
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>Upcoming</title><style>{css}</style>"
            f"<h1>Upcoming from your Simkl watchlist</h1><p>Updated {escape(stamp)} ({escape(TZ_NAME)}).</p>"
            + "".join(out))


# ---------- Output: Stremio / Nuvio addon ----------

def poster_url(p):
    return f"https://simkl.in/posters/{p}_m.jpg" if p else None


def make_catalog(items, nxt):
    """items = watchlist titles of one kind. Titles with a date first, then TBC."""
    def sortkey(w):
        r = nxt.get(w["simkl"])
        return (0, r["when"]) if r else (1, datetime.max.replace(tzinfo=timezone.utc))
    metas, skipped = [], 0
    for w in sorted(items, key=lambda w: (sortkey(w), w["title"].lower())):
        if not w.get("imdb"):
            skipped += 1  # Nuvio needs an IMDb id to open the title
            continue
        r = nxt.get(w["simkl"])
        info = f"{r['what']} · {when_text(r)}" if r else "TBC"
        meta = {"id": w["imdb"], "type": "movie" if w["kind"] == "movie" else "series",
                "name": w["title"], "releaseInfo": info,
                "description": f"Next: {info}" if r else "Release date to be confirmed (TBC)"}
        if poster_url(w.get("poster")):
            meta["poster"] = poster_url(w["poster"])
        metas.append(meta)
    return {"metas": metas}, skipped


def manifest():
    return {"id": "community.simkl.upcoming", "version": VERSION, "name": "Simkl Upcoming",
            "description": "Upcoming episodes and releases from your Simkl watchlist (TBC when unknown).",
            "resources": ["catalog"], "types": ["movie", "series"],
            "catalogs": [{"type": "series", "id": "simkl-upcoming-series", "name": "Upcoming Episodes"},
                         {"type": "movie", "id": "simkl-upcoming-movies", "name": "Upcoming Movies"}]}


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def build_site(watchlist, cal, out="site", today=None, now=None):
    today = today or datetime.now(tz()).date()
    rows, tbc = build_entries(watchlist, cal, today)
    nxt = next_by_title(rows)
    write(f"{out}/index.html", make_html(rows, tbc, now, today))
    write(f"{out}/calendar.ics", make_ics(rows, now))
    write(f"{out}/manifest.json", json.dumps(manifest(), indent=1))
    skipped = 0
    for kind, cid, typ in (("series", "simkl-upcoming-series", "series"),
                           ("movie", "simkl-upcoming-movies", "movie")):
        cat, sk = make_catalog([w for w in watchlist if w["kind"] == kind], nxt)
        skipped += sk
        write(f"{out}/catalog/{typ}/{cid}.json", json.dumps(cat))
    return rows, tbc, skipped


def main():
    client_id, token = os.environ.get("SIMKL_CLIENT_ID"), os.environ.get("SIMKL_TOKEN")
    if not client_id or not token:
        sys.exit("Set SIMKL_CLIENT_ID and SIMKL_TOKEN.")
    out = sys.argv[1] if len(sys.argv) > 1 else "site"
    today = datetime.now(tz()).date()
    watchlist = fetch_watchlist(client_id, token)
    cal = fetch_calendar(client_id, today)
    rows, tbc, skipped = build_site(watchlist, cal, out, today)
    print(f"{len(watchlist)} titles: {len({r['w']['simkl'] for r in rows})} with dates "
          f"({len(rows)} events), {len(tbc)} TBC, {skipped} left out of the Nuvio catalogue (no IMDb id).")


if __name__ == "__main__":
    main()
