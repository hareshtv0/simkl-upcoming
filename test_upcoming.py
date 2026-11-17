import json, os, tempfile, unittest
from datetime import date, datetime, timezone
import upcoming as u

WL = [
 {"simkl": 1, "imdb": "tt1", "tmdb": 1, "title": "Show A", "year": 2024, "kind": "series", "status": "watching", "poster": "ab/cd"},
 {"simkl": 2, "imdb": "tt2", "tmdb": 2, "title": "Movie B", "year": 2026, "kind": "movie", "status": "plantowatch", "poster": None},
 {"simkl": 3, "imdb": None, "tmdb": 3, "title": "Anime C", "year": 2026, "kind": "series", "status": "plantowatch", "poster": None},
 {"simkl": 4, "imdb": "tt4", "tmdb": 4, "title": "Unknown D", "year": 2027, "kind": "movie", "status": "plantowatch", "poster": None},
 {"simkl": 5, "imdb": "tt5", "tmdb": 5, "title": "Not in list", "year": 2020, "kind": "series", "status": "watching", "poster": None},
]
CAL = {
 "tv": {"calendar": [
   {"simkl_id": 1, "date": "2026-11-21T13:00:00Z", "finale_type": 1, "episode": {"season": 2, "episode": 8, "title": "End", "runtime": 45, "url": "x"}},
   {"simkl_id": 1, "date": "2026-11-14T13:00:00Z", "finale_type": None, "episode": {"season": 2, "episode": 7, "title": "Past", "runtime": 45, "url": "x"}},
   {"simkl_id": 99, "date": "2026-11-22T13:00:00Z", "finale_type": None, "episode": {"season": 1, "episode": 1, "title": None, "runtime": None, "url": "x"}}],
   "metadata": {}},
 "anime": {"calendar": [
   {"simkl_id": 3, "date": "2026-11-30T16:30:00Z", "finale_type": None, "episode": {"episode": 3, "title": None, "runtime": None, "url": "x"}}],
   "metadata": {}},
 "movie_release": {"calendar": [
   {"simkl_id": 2, "date": "2026-12-04T00:00:00Z", "finale_type": None}],
   "metadata": {"2": {"dvd_date": "01/15/2027"}}},
}
TODAY = date(2026, 11, 17)

class T(unittest.TestCase):
    def test_entries(self):
        rows, tbc = u.build_entries(WL, CAL, TODAY)
        got = [(r["w"]["title"], r["what"]) for r in rows]
        self.assertEqual(got, [("Show A", "S2E8"), ("Anime C", "E3"), ("Movie B", "Release"), ("Movie B", "Home release")])
        self.assertEqual([w["title"] for w in tbc], ["Not in list", "Unknown D"])  # unmatched -> TBC
    def test_build_site(self):
        with tempfile.TemporaryDirectory() as d:
            rows, tbc, skipped = u.build_site(WL, CAL, d, TODAY, datetime(2026, 11, 17, tzinfo=timezone.utc))
            self.assertEqual(skipped, 1)  # Anime C has no imdb id
            series = json.load(open(f"{d}/catalog/series/simkl-upcoming-series.json"))["metas"]
            self.assertEqual([m["name"] for m in series], ["Show A"])
            self.assertTrue(series[0]["releaseInfo"].startswith("S2E8"))
            self.assertEqual(series[0]["poster"], "https://simkl.in/posters/ab/cd_m.jpg")
            movies = json.load(open(f"{d}/catalog/movie/simkl-upcoming-movies.json"))["metas"]
            self.assertEqual([m["name"] for m in movies], ["Movie B"])
            self.assertTrue(movies[0]["releaseInfo"].startswith("Home release"))
            ics = open(f"{d}/calendar.ics", newline="").read()
            self.assertIn("BEGIN:VCALENDAR\r\n", ics)
            self.assertEqual(ics.count("BEGIN:VEVENT"), 4)
            self.assertIn("SUMMARY:Show A S2E8 - End [finale]", ics)
            self.assertIn("DTSTART:20261121T130000Z", ics)
            self.assertIn("DTEND:20261121T134500Z", ics)
            self.assertIn("DTSTART;VALUE=DATE:20270115", ics)
            page = open(f"{d}/index.html").read()
            self.assertIn("TBC", page)
            self.assertIn("<h2>November 2026</h2>", page)
            self.assertIn("<h2>January 2027</h2>", page)
            self.assertIn("Show A <small>S2E8 (finale)</small>", page)
            m = json.load(open(f"{d}/manifest.json"))
            self.assertEqual(m["resources"], ["catalog"])
    def test_fold(self):
        self.assertTrue(all(len(l.encode()) <= 75 for l in u.fold("SUMMARY:" + "é" * 100).split("\r\n")))
    def test_months(self):
        self.assertEqual(list(u.month_list(date(2026, 11, 17))), [(2026,11),(2026,12),(2027,1),(2027,2)])

if __name__ == "__main__":
    unittest.main()
