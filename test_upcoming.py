import json, tempfile, unittest
from datetime import date, datetime, timezone
from unittest import mock
import upcoming as u

WL = [
 {"simkl": 1, "imdb": "tt1", "tmdb": 1, "title": "Weekly Show", "year": 2024, "kind": "series", "status": "watching", "poster": "ab/cd"},
 {"simkl": 2, "imdb": "tt2", "tmdb": 22, "title": "Movie Digital Soon", "year": 2026, "kind": "movie", "status": "plantowatch", "poster": None},
 {"simkl": 3, "imdb": None, "tmdb": 3, "title": "Anime No Imdb", "year": 2026, "kind": "series", "status": "plantowatch", "poster": None},
 {"simkl": 4, "imdb": "tt4", "tmdb": 44, "title": "Movie Already Digital", "year": 2025, "kind": "movie", "status": "plantowatch", "poster": None},
 {"simkl": 5, "imdb": "tt5", "tmdb": 55, "title": "Ended Show", "year": 2020, "kind": "series", "status": "watching", "poster": None},
 {"simkl": 6, "imdb": "tt6", "tmdb": 66, "title": "New Season Far Off", "year": 2021, "kind": "series", "status": "watching", "poster": None},
 {"simkl": 7, "imdb": "tt7", "tmdb": 77, "title": "Cinema Only Movie", "year": 2026, "kind": "movie", "status": "plantowatch", "poster": None},
]
CAL = {
 "tv": {"calendar": [
   {"simkl_id": 1, "date": "2026-11-21T13:00:00Z", "finale_type": 1, "episode": {"season": 2, "episode": 8, "title": "End", "runtime": 45, "url": "x"}},
   {"simkl_id": 1, "date": "2026-11-14T13:00:00Z", "finale_type": None, "episode": {"season": 2, "episode": 7, "title": "Past", "runtime": 45, "url": "x"}},
   {"simkl_id": 6, "date": "2027-01-20T13:00:00Z", "finale_type": None, "episode": {"season": 3, "episode": 1, "title": None, "runtime": None, "url": "x"}},
   {"simkl_id": 99, "date": "2026-11-22T13:00:00Z", "finale_type": None, "episode": {"season": 1, "episode": 1, "title": None, "runtime": None, "url": "x"}}],
   "metadata": {}},
 "anime": {"calendar": [
   {"simkl_id": 3, "date": "2026-11-30T16:30:00Z", "finale_type": None, "episode": {"episode": 3, "title": None, "runtime": None, "url": "x"}}],
   "metadata": {}},
}
TODAY = date(2026, 11, 17)
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
DIGITAL = {2: date(2026, 12, 20)}

def tmdb_payload(types):
    return {"results": [{"iso_3166_1": "US", "release_dates": [{"type": t, "release_date": d + "T00:00:00.000Z"} for t, d in types]}]}

class T(unittest.TestCase):
    def test_entries_movies_only_from_digital_date(self):
        rows, tbc = u.build_entries(WL, CAL, TODAY, DIGITAL)
        got = [(r["w"]["title"], r["what"]) for r in rows]
        self.assertEqual(got, [("Weekly Show", "S2E8"), ("Anime No Imdb", "E3"), ("Movie Digital Soon", "Digital release"), ("New Season Far Off", "S3E1")])
        self.assertEqual(sorted(w["title"] for w in tbc), ["Cinema Only Movie", "Ended Show", "Movie Already Digital"])
    def test_catalogue(self):
        with tempfile.TemporaryDirectory() as d:
            rows, tbc, skipped = u.build_site(WL, CAL, d, TODAY, NOW, DIGITAL)
            self.assertEqual(skipped, 1)  # Anime No Imdb
            with open(f"{d}/catalog/series/simkl-upcoming-series.json") as f:
                series = json.load(f)["metas"]
            self.assertEqual([m["name"] for m in series], ["Weekly Show"])  # ended / far-off season / TBC excluded
            self.assertEqual(series[0]["poster"], "https://simkl.in/posters/ab/cd_m.jpg")
            with open(f"{d}/catalog/movie/simkl-upcoming-movies.json") as f:
                movies = json.load(f)["metas"]
            self.assertEqual([m["name"] for m in movies], ["Movie Digital Soon"])
            self.assertTrue(movies[0]["releaseInfo"].startswith("Digital release"))
            with open(f"{d}/calendar.ics", newline="") as f:
                ics = f.read()
            self.assertIn("SUMMARY:Weekly Show S2E8 - End [finale]", ics)
            self.assertIn("DTSTART;VALUE=DATE:20261220", ics)
            self.assertIn("DTEND:20261121T134500Z", ics)
            with open(f"{d}/index.html") as f:
                page = f.read()
            self.assertIn("<h2>TBC</h2>", page)
            self.assertIn("<h2>December 2026</h2>", page)
    def test_tmdb_digital(self):
        cases = {
            "future digital": (tmdb_payload([(3, "2026-11-20"), (4, "2026-12-25")]), date(2026, 12, 25)),
            "already digital": (tmdb_payload([(3, "2026-01-01"), (4, "2026-03-01")]), None),
            "cinema only": (tmdb_payload([(3, "2026-12-01"), (5, "2027-01-01")]), None),
            "earliest of two": (tmdb_payload([(4, "2027-02-01"), (4, "2026-12-30")]), date(2026, 12, 30)),
        }
        for name, (payload, want) in cases.items():
            with mock.patch.object(u, "get_json", return_value=payload):
                self.assertEqual(u.tmdb_digital_date(1, "k" * 32, TODAY), want, name)
        with mock.patch.object(u, "get_json", return_value=None):
            self.assertIsNone(u.tmdb_digital_date(1, "k", TODAY))
    def test_tmdb_bearer_token(self):
        with mock.patch.object(u, "get_json", return_value=None) as g:
            u.tmdb_digital_date(5, "t" * 60, TODAY)
            self.assertEqual(g.call_args[0][1], {"Authorization": "Bearer " + "t" * 60})
            self.assertNotIn("api_key", g.call_args[0][0])
    def test_english_title_and_poster(self):
        payload = {"name": "Case Closed", "poster_path": "/abc.jpg"}
        with mock.patch.object(u, "get_json", return_value=payload) as g:
            self.assertEqual(u.tmdb_english("series", 9, "k" * 32),
                             ("Case Closed", "https://image.tmdb.org/t/p/w342/abc.jpg"))
            self.assertIn("tv/9?api_key=", g.call_args[0][0])
            self.assertIn("language=en-US", g.call_args[0][0])
        w = {"tmdb": 9, "kind": "series", "title": "Meitantei Conan", "poster": "x/y"}
        with mock.patch.object(u, "get_json", return_value=payload):
            u.enrich_titles([w], "k" * 32)
        self.assertEqual((w["title"], w["poster_url"]), ("Case Closed", "https://image.tmdb.org/t/p/w342/abc.jpg"))
        keep = {"tmdb": 9, "kind": "series", "title": "Keep Me", "poster": None}
        with mock.patch.object(u, "get_json", return_value=None):
            u.enrich_titles([keep], "k" * 32)
        self.assertEqual(keep["title"], "Keep Me")
    def test_singapore_time(self):
        r = {"when": datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc), "allday": False}
        self.assertEqual(u.when_text(r), "Sat 21 Nov 17:00")
    def test_overlay_and_hosted_poster(self):
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (342, 513), (120, 90, 200)).save(buf, "PNG")
        jpg = u.make_overlay(buf.getvalue(), "Sat 21 Nov, 17:00", "E1221")
        im = Image.open(io.BytesIO(jpg))
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(im.size, (500, 750))
        bottom = im.getpixel((5, 745))
        self.assertLess(max(bottom), 100)  # dark banner at the bottom
        self.assertGreater(bottom[2], bottom[1])  # tinted from the purple poster
        top = im.getpixel((5, 5))
        self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(top, (120, 90, 200))))  # artwork untouched
        self.assertEqual(u.banner_lines({"when": datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc), "allday": False, "what": "E1221"}),
                         ("Sat 21 Nov, 17:00", "E1221"))
        self.assertEqual(u.banner_lines({"when": datetime(2026, 12, 23, tzinfo=timezone.utc), "allday": True, "what": "Digital release"}),
                         ("Wed 23 Dec", "Digital release"))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(u, "get_bytes", return_value=buf.getvalue()):
            wl = [dict(x, poster_url="https://image.tmdb.org/t/p/w342/p.jpg") for x in WL]
            u.build_site(wl, CAL, d, TODAY, NOW, DIGITAL, "https://x.github.io/repo")
            with open(f"{d}/catalog/series/simkl-upcoming-series.json") as f:
                meta = json.load(f)["metas"][0]
            import re
            self.assertRegex(meta["poster"], r"^https://x\.github\.io/repo/posters/1-[0-9a-f]{8}\.jpg$")
            self.assertNotIn("releaseInfo", meta)  # the poster already shows the date
            import os
            self.assertTrue(os.path.exists(f"{d}/posters/" + meta["poster"].rsplit("/", 1)[1]))
            with open(f"{d}/index.html") as f:
                self.assertIn("not endorsed or certified by TMDB", f.read())
    def test_overlay_falls_back_when_download_fails(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(u, "get_bytes", side_effect=OSError("boom")):
            wl = [dict(x, poster_url="https://image.tmdb.org/t/p/w342/p.jpg") for x in WL]
            u.build_site(wl, CAL, d, TODAY, NOW, DIGITAL, "https://x.github.io/repo")
            with open(f"{d}/catalog/series/simkl-upcoming-series.json") as f:
                meta = json.load(f)["metas"][0]
            self.assertEqual(meta["poster"], "https://image.tmdb.org/t/p/w342/p.jpg")
            self.assertTrue(meta["releaseInfo"].startswith("S2E8"))  # no banner, so keep the text
    def test_banner_colour_follows_poster(self):
        from PIL import Image
        red = u.banner_colour(Image.new("RGB", (100, 150), (200, 30, 30)))
        self.assertGreater(red[0], red[1]); self.assertGreater(red[0], red[2]); self.assertLess(max(red), 100)
        grey = u.banner_colour(Image.new("RGB", (100, 150), (128, 128, 128)))
        self.assertLess(max(grey), 100)
    def test_fold_and_months(self):
        self.assertTrue(all(len(l.encode()) <= 75 for l in u.fold("SUMMARY:" + "é" * 100).split("\r\n")))
        self.assertEqual(list(u.month_list(date(2026, 11, 17))), [(2026,11),(2026,12),(2027,1),(2027,2)])

if __name__ == "__main__":
    unittest.main()
