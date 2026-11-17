#!/usr/bin/env python3
"""Build an "upcoming releases" calendar from your Simkl watchlist.

Reads your Simkl Plan to Watch (movies, shows, anime) and Watching (shows, anime) lists,
looks each title up in Simkl's public calendar files, and writes to ./site:

  index.html     readable list, soonest first, TBC at the bottom
  calendar.ics   subscribe in Google/Apple Calendar
  manifest.json  Stremio-style addon with two catalogues ("Upcoming" for series and movies);
  catalog/...    Nuvio can install it under Settings > Addons

A title with no date in the calendar window is shown as TBC (to be confirmed).
Standard library only. Needs SIMKL_CLIENT_ID and SIMKL_TOKEN (same as lb2simkl.py), plus a free
TMDB_API_KEY for movie digital release dates. Movies without a known future digital date are left out;
series only appear in the Nuvio rows while their next episode is within RUNNING_DAYS.
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
RUNNING_DAYS = 14  # a series counts as running if its next episode is within this many days


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
    for typ in ("tv", "anime"):
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


# ---------- TMDB: digital release dates for movies ----------

def tmdb_get(path, key, params=""):
    url = f"https://api.themoviedb.org/3/{path}"
    headers = {}
    if len(key) > 40:  # v4 read-access token
        headers["Authorization"] = f"Bearer {key}"
        url += f"?{params}" if params else ""
    else:
        url += f"?api_key={key}" + (f"&{params}" if params else "")
    return get_json(url, headers)


def tmdb_digital_date(tmdb_id, key, today):
    """Earliest DIGITAL release (TMDB release type 4) in any region that is still in the future.

    Returns None when TMDB has no digital date yet, or when the film is already out digitally.
    """
    data = tmdb_get(f"movie/{tmdb_id}/release_dates", key)
    if not data:
        return None
    dates = []
    for region in data.get("results") or []:
        for rd in region.get("release_dates") or []:
            if rd.get("type") == 4 and rd.get("release_date"):
                try:
                    dates.append(datetime.fromisoformat(rd["release_date"].replace("Z", "+00:00")).date())
                except ValueError:
                    pass
    if not dates:
        return None
    first = min(dates)
    return first if first >= today else None


def tmdb_english(kind, tmdb_id, key):
    """(English title, poster url) from TMDB, the same artwork source Nuvio shows. Either may be None."""
    data = tmdb_get(f"{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}", key, "language=en-US")
    if not data:
        return None, None
    title = data.get("title") or data.get("name")
    poster = f"https://image.tmdb.org/t/p/w342{data['poster_path']}" if data.get("poster_path") else None
    return title, poster


def enrich_titles(items, key):
    """Replace the title with TMDB's English one and attach a TMDB poster, when TMDB knows the title."""
    for w in items:
        if not w.get("tmdb"):
            continue
        try:
            title, poster = tmdb_english(w["kind"], w["tmdb"], key)
        except Exception as e:
            print(f"Note: TMDB lookup failed for {w['title']} ({e})")
            continue
        if title:
            w["title"] = title
        if poster:
            w["poster_url"] = poster


def fetch_digital(watchlist, key, today):
    """{simkl_id: date} for movies with a future digital release date."""
    out = {}
    for w in watchlist:
        if w["kind"] != "movie" or not w.get("tmdb"):
            continue
        try:
            d = tmdb_digital_date(w["tmdb"], key, today)
        except Exception as e:
            print(f"Note: TMDB lookup failed for {w['title']} ({e})")
            continue
        if d:
            out[w["simkl"]] = d
    return out


# ---------- Join ----------

def ep_label(ep):
    if not ep:
        return None
    season, num = ep.get("season"), ep.get("episode")
    if num is None:
        return None
    return f"S{season}E{num}" if season is not None else f"E{num}"


def build_entries(watchlist, cal, today, digital=None):
    """Return (dated, tbc). Episodes come from Simkl's calendar; movies only from a digital release date."""
    by_id = {w["simkl"]: w for w in watchlist}
    rows = []
    for typ in ("tv", "anime"):
        for item in cal[typ]["calendar"]:
            w = by_id.get(item.get("simkl_id"))
            ep = item.get("episode")
            if not w or not ep or w["kind"] != "series":
                continue
            when = parse_dt(item["date"])
            if when.astimezone(tz()).date() < today:
                continue
            rows.append({"w": w, "when": when, "allday": False, "what": ep_label(ep) or "Episode",
                         "ep": ep, "finale": item.get("finale_type")})
    for sid, d in (digital or {}).items():
        w = by_id.get(sid)
        if w and d >= today:
            rows.append({"w": w, "when": datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                         "allday": True, "what": "Digital release", "ep": None, "finale": None})
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
            + "".join(out) + "<p><small>This product uses the TMDB API but is not endorsed or certified by TMDB.</small></p>")


# ---------- Poster overlay (date and time printed on the poster) ----------

FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")


def get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP}/{VERSION}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def load_font(size):
    from PIL import ImageFont
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def fit_font(draw, text, max_w, start, floor=14):
    size = start
    while size > floor:
        font = load_font(size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return load_font(floor)


def banner_colour(img):
    """A dark shade of the poster's own colour, for the banner background."""
    import colorsys
    from PIL import Image
    h, w = img.size[1], img.size[0]
    lower = img.crop((0, int(h * 0.55), w, h))
    r, g, b = lower.resize((1, 1), Image.BOX).getpixel((0, 0))
    hue, sat, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    sat = min(max(sat, 0.25), 0.75)
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, 0.20))


def make_overlay(image_bytes, line1, line2):
    """Return JPEG bytes: the poster with a banner, tinted from the poster, holding the date and time."""
    import io
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if img.width != 500:
        img = img.resize((500, round(img.height * 500 / img.width)))
    w, h = img.size
    band = round(h * 0.15)
    colour = banner_colour(img)
    fade = round(band * 0.5)  # soft blend from the artwork into the banner
    grad = Image.new("RGBA", (w, fade))
    gd = ImageDraw.Draw(grad)
    for y in range(fade):
        gd.line([(0, y), (w, y)], fill=colour + (round(255 * (y + 1) / fade * 0.9),))
    img = img.convert("RGBA")
    img.alpha_composite(grad, (0, h - band - fade))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, h - band, w, h], fill=colour + (255,))
    f1 = fit_font(draw, line1, w * 0.78, round(band * 0.40))
    f2 = fit_font(draw, line2, w * 0.78, round(band * 0.27))
    draw.text((w / 2, h - band * 0.60), line1, font=f1, fill=(255, 255, 255), anchor="mm")
    draw.text((w / 2, h - band * 0.22), line2, font=f2, fill=(222, 220, 212), anchor="mm")
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=88)
    return out.getvalue()


def banner_lines(r):
    local = r["when"].astimezone(tz()) if not r["allday"] else r["when"]
    if r["allday"]:
        return local.strftime("%a %d %b"), r["what"]
    return local.strftime("%a %d %b, %H:%M"), r["what"]


def poster_source(w):
    src = w.get("poster_url") or poster_url(w.get("poster"))
    return src.replace("/w342/", "/w500/") if src else None


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def make_poster_fn(out, base_url):
    """Return poster_fn(w, r) -> hosted URL of the poster with the date printed on it, or None."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow not installed: posters will have no date banner.")
        return None

    def fn(w, r):
        src = poster_source(w)
        if not src:
            return None
        try:
            data = make_overlay(get_bytes(src), *banner_lines(r))
        except Exception as e:
            print(f"Note: no banner for {w['title']} ({e})")
            return None
        name = f"posters/{w['simkl']}-{r['when'].strftime('%Y%m%d%H%M')}.jpg"
        write_bytes(f"{out}/{name}", data)
        return f"{base_url.rstrip('/')}/{name}"
    return fn


# ---------- Output: Stremio / Nuvio addon ----------

def poster_url(p):
    return f"https://simkl.in/posters/{p}_m.jpg" if p else None


def make_catalog(items, nxt, today=None, max_days=RUNNING_DAYS, poster_fn=None):
    """items = watchlist titles of one kind. Only titles with a known date, soonest first."""
    def sortkey(w):
        r = nxt.get(w["simkl"])
        return (0, r["when"]) if r else (1, datetime.max.replace(tzinfo=timezone.utc))
    metas, skipped = [], 0
    for w in sorted(items, key=lambda w: (sortkey(w), w["title"].lower())):
        r = nxt.get(w["simkl"])
        if not r:
            continue  # no date: stays out of the Nuvio rows (still on the web page)
        if w["kind"] == "series" and today is not None:
            if (r["when"].astimezone(tz()).date() - today).days > max_days:
                continue  # next episode is far off: new season pending, not a running show
        if not w.get("imdb"):
            skipped += 1  # Nuvio needs an IMDb id to open the title
            continue
        info = f"{r['what']} · {when_text(r)}"
        banner = poster_fn(w, r) if poster_fn else None
        meta = {"id": w["imdb"], "type": "movie" if w["kind"] == "movie" else "series",
                "name": w["title"], "description": f"Next: {info}"}
        if not banner:
            meta["releaseInfo"] = info  # no date printed on the poster, so keep the text line
        poster = banner or w.get("poster_url") or poster_url(w.get("poster"))
        if poster:
            meta["poster"] = poster
        metas.append(meta)
    return {"metas": metas}, skipped


def manifest():
    return {"id": "community.simkl.upcoming", "version": VERSION, "name": "Simkl Upcoming",
            "description": "Upcoming episodes and releases from your Simkl watchlist. This product uses the TMDB API but is not endorsed or certified by TMDB.",
            "resources": ["catalog"], "types": ["movie", "series"],
            "catalogs": [{"type": "series", "id": "simkl-upcoming-series", "name": "Upcoming Episodes"},
                         {"type": "movie", "id": "simkl-upcoming-movies", "name": "Upcoming Movies"}]}


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def build_site(watchlist, cal, out="site", today=None, now=None, digital=None, poster_base=None):
    today = today or datetime.now(tz()).date()
    rows, tbc = build_entries(watchlist, cal, today, digital)
    nxt = next_by_title(rows)
    write(f"{out}/index.html", make_html(rows, tbc, now, today))
    write(f"{out}/calendar.ics", make_ics(rows, now))
    write(f"{out}/manifest.json", json.dumps(manifest(), indent=1))
    skipped = 0
    poster_fn = make_poster_fn(out, poster_base) if poster_base else None
    for kind, cid, typ in (("series", "simkl-upcoming-series", "series"),
                           ("movie", "simkl-upcoming-movies", "movie")):
        cat, sk = make_catalog([w for w in watchlist if w["kind"] == kind], nxt, today, poster_fn=poster_fn)
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
    tmdb_key = os.environ.get("TMDB_API_KEY")
    if tmdb_key:
        digital = fetch_digital(watchlist, tmdb_key, today)
    else:
        digital = {}
        print("TMDB_API_KEY not set: movies are left out (no digital release dates available).")
    if tmdb_key:
        listed, _ = build_entries(watchlist, cal, today, digital)
        by_id = {r["w"]["simkl"]: r["w"] for r in listed}
        enrich_titles(by_id.values(), tmdb_key)
    rows, tbc, skipped = build_site(watchlist, cal, out, today, None, digital, os.environ.get("SITE_URL"))
    print(f"{len(watchlist)} titles: {len({r['w']['simkl'] for r in rows})} with dates "
          f"({len(rows)} events), {len(tbc)} TBC (web page only), {skipped} dated but left out of the Nuvio catalogue (no IMDb id).")


if __name__ == "__main__":
    main()
