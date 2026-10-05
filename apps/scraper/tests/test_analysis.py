"""Insight engine: media helpers, selection, media.reel, analyze.reel, analyze.creator, evals.

Temp databases only. The claude CLI is never run (llm._execute is mocked); the one ffmpeg test makes
its own synthetic clip and is skipped when ffmpeg is missing.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from microindia_scraper import handlers  # noqa: F401  (registers kinds)
from microindia_scraper.analysis import db, evals, fetch, media, selection, transcribe
from microindia_scraper.analysis.creator import build_dossier, cadence_from_ids, dossier_problems
from microindia_scraper.analysis.reel import analyze, dossier_follow_up, evidence_problems
from microindia_scraper.analysis.spec import load
from microindia_scraper.models import ContentObservation, ProfileCapture, ProfileObservation
from microindia_scraper.niches import NICHES
from microindia_scraper.runtime import Done, Skip, TaskStore, registered_kinds
from microindia_scraper.runtime.registry import TaskContext, get_handler
from microindia_scraper.store import CaptureStore

MEDIA_ITEM = {
    "code": "DdMJKW6SQGE", "pk": "3984600064529596804", "media_type": 2, "product_type": "clips",
    "taken_at": 1789221390, "video_duration": 25.797, "play_count": 6508, "ig_play_count": 5432,
    "like_count": 28, "comment_count": 13, "has_audio": True, "is_paid_partnership": False,
    "coauthor_producers": [], "original_width": 720, "original_height": 1280, "original_lang_for_translations": "ta",
    "view_count": None, "sponsor_tags": None,
    "video_versions": [{"url": "https://cdn.example/v.mp4", "width": 720, "height": 1280, "bandwidth": 1792440}],
    "image_versions2": {"candidates": [{"url": "https://cdn.example/c.jpg", "width": 640, "height": 1136}]},
    "clips_metadata": {"audio_type": "original_sounds", "original_sound_info": {
        "original_audio_title": "Original audio", "audio_asset_id": "27891677940471687", "ig_artist": {"username": "rasikumargalle"}}},
    "usertags": {"in": [{"user": {"username": "tide.india"}}]}, "location": {"name": "Chennai, India"},
    "caption": {"text": "#ad\n\nTide power gel for my regular laundry"}, "user": {"username": "mummastessa"},
    "video_dash_manifest": "<MPD/>",
}

# Valid-alphabet shortcodes with increasing media ids (newer = larger).
CODES = ["DAAAAAAAAA1", "DAAAAAAAAA2", "DAAAAAAAAA3", "DAAAAAAAAA4", "DAAAAAAAAA5", "DAAAAAAAAA6",
         "DBAAAAAAAA1", "DBAAAAAAAA2", "DBAAAAAAAA3", "DBAAAAAAAA4", "DCAAAAAAAA1", "DCAAAAAAAA2",
         "DCAAAAAAAA3", "DCAAAAAAAA4", "DDAAAAAAAA1", "DDAAAAAAAA2", "DDAAAAAAAA3", "DDAAAAAAAA4"]


def reel_result(niche="food", brand=None):
    return {
        "summary": "A cook makes dal. It is a simple recipe.",
        "hook": {"visual": "pot on stove", "spoken": None, "on_screen_text": None, "technique": "close-up", "strength": 3, "evidence": ["H0"]},
        "format": {"primary": "recipe", "description": "recipe", "evidence": ["K1"]},
        "topic": {"niche": niche, "secondary_niches": [], "subject": "dal", "evidence": ["caption"]},
        "audio": {"kind": "speech", "spoken_languages": [{"language": "Hindi", "share": "dominant", "evidence": ["T0-2"]}], "music": None, "evidence": ["T0-2"]},
        "on_screen_text": [],
        "products_brands": [{"name": brand, "kind": "brand", "how": "visible", "featured": True, "evidence": ["K1"]}] if brand else [],
        "sponsorship": {"disclosed": bool(brand), "disclosure": None, "detected": bool(brand), "brand": brand, "confidence": "high", "evidence": ["meta:paid_partnership"]},
        "production": {"quality": 3, "notes": "clean", "evidence": ["K1"]},
        "face_to_camera": {"value": True, "evidence": ["H0"]},
        "setting": {"description": "home kitchen", "city_cues": []},
        "tone": {"labels": ["warm"], "description": "warm", "evidence": ["T0-2"]},
        "target_audience": {"description": "home cooks", "evidence": ["caption"]},
        "cta": {"present": False, "text": None, "kind": None, "evidence": ["caption"]},
        "brand_safety": {"flags": [], "safe_for_most_brands": True},
        "performance": {"verdict": "in_line", "explanation": "1.0x", "evidence": ["meta:audio"]},
        "evidence": [{"ref": "K9", "observation": "made-up frame"}],
        "unknowns": [],
    }


def claude_output(structured, *, stream=True):
    envelope = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(structured),
                "structured_output": structured, "total_cost_usd": 0.05,
                "modelUsage": {"claude-sonnet-5-5": {"outputTokens": 900}}}
    stdout = "\n".join([json.dumps({"type": "system", "subtype": "init"}), json.dumps(envelope)]) if stream else json.dumps(envelope)
    return mock.Mock(returncode=0, stdout=stdout, stderr="")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tmp.name, "data", "t.sqlite3")
        os.makedirs(os.path.dirname(self.database))
        self.env = mock.patch.dict(os.environ, {"MICROINDIA_CLAUDE_BIN": "/usr/bin/true", "ANTHROPIC_API_KEY": "",
                                                "MICROINDIA_DATABASE": self.database})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def capture(self, username, posts, *, capture_id="c1", captured_at="2026-10-05T00:00:00Z", followers=20000):
        store = CaptureStore(self.database)
        store.create_capture(ProfileCapture(capture_id=capture_id, candidate_key=f"instagram:{username}",
                                            profile_url=f"https://www.instagram.com/{username}/", captured_at=captured_at,
                                            schema_version="v"))
        store.save_profile_snapshot(ProfileObservation(capture_id=capture_id, handle=username, follower_count=followers,
                                                       bio_text="Home cook"), captured_at)
        for index, post in enumerate(posts, start=1):
            store.save_content_snapshot(ContentObservation(capture_id=capture_id, content_index=index, **post), captured_at)
        store.close()

    def reel_posts(self, username, count=18, likes=None):
        posts = []
        for index, code in enumerate(CODES[:count]):
            posts.append({"permalink": f"https://www.instagram.com/{username}/reel/{code}/", "platform_content_id": code,
                          "content_type": "reel", "like_count": (likes or {}).get(code, 100), "comment_count": 5,
                          "caption_text": "Dal recipe"})
        return posts

    def cached_assets(self, shortcode, creator="cook"):
        paths = media.paths_for(self.database, shortcode)
        os.makedirs(paths["frames"], exist_ok=True)
        frames, hooks = [], []
        for name in ("H0", "H1", "K1"):
            path = os.path.join(paths["frames"], f"{name}.jpg")
            with open(path, "wb") as handle:
                handle.write(b"\xff\xd8" + name.encode() + shortcode.encode())
            (hooks if name.startswith("H") else frames).append({"id": name, "t": 0.0 if name == "H0" else 1.0, "path": path})
        transcribe.save({"version": "whisper-v2", "language": "hi", "text": "dal",
                         "segments": [{"start": 0.0, "end": 2.0, "text": "आज दाल बनाते हैं", "no_speech_prob": 0.01,
                                       "avg_logprob": -0.1, "compression_ratio": 2.6, "speech": True}],
                         "speech_detected": True, "speech_seconds": 2.0}, paths["transcript"])
        connection = db.connect(self.database)
        db.save_media(connection, {"shortcode": shortcode, "creator": creator, "owner_username": creator, "duration": 10.0,
                                   "play_count": 1000, "like_count": 50, "comment_count": 5, "caption": "Dal recipe",
                                   "audio_json": {"type": "original_sounds"}, "is_paid_partnership": False})
        db.save_assets(connection, shortcode, creator=creator, frames_json=frames, hook_frames_json=hooks,
                       transcript_path=paths["transcript"], speech_detected=True)
        connection.close()


class MediaHelpersTest(unittest.TestCase):
    def test_shortcode_pk_round_trip_matches_instagram(self):
        self.assertEqual(media.shortcode_to_pk("Dd_AFsYydDi"), 3998915385386193122)  # from the media-info API
        self.assertEqual(media.pk_to_shortcode(3998915385386193122), "Dd_AFsYydDi")
        self.assertEqual(media.shortcode_of("https://www.instagram.com/mummastessa/reel/Dd_AFsYydDi/?igsh=x"), "Dd_AFsYydDi")
        self.assertEqual(media.shortcode_of("https://www.instagram.com/reels/Dd_AFsYydDi/"), "Dd_AFsYydDi")
        self.assertEqual(media.owner_of_permalink("https://www.instagram.com/berlin_mathew/reel/Dc_KCG/"), "berlin_mathew")
        self.assertEqual(media.shortcode_of("https://www.instagram.com/berlin_mathew/reel/Dc_KCGyppYEMsDerMyVKwf3hRXRaf8JDzafuZo0/"), "Dc_KCGyppYE")
        with self.assertRaises(ValueError):
            media.shortcode_of("not a code!")

    def test_shortcode_timestamp_is_the_upload_time(self):
        # taken_at from the API was 1790927969; the id says a minute and a half earlier (upload vs publish).
        self.assertAlmostEqual(media.shortcode_timestamp("Dd_AFsYydDi"), 1790927969, delta=300)

    def test_keyframe_times_cover_the_reel(self):
        self.assertEqual(media.choose_keyframe_times(0, []), [])
        talking_head = media.choose_keyframe_times(40.0, [])
        self.assertGreaterEqual(len(talking_head), 3)
        self.assertTrue(all(3.0 < t < 40.0 for t in talking_head))
        busy = media.choose_keyframe_times(60.0, [float(t) for t in range(4, 58)])
        self.assertEqual(len(busy), media.MAX_KEYFRAMES)


@unittest.skipUnless(shutil.which("ffmpeg") or os.path.exists("/opt/homebrew/bin/ffmpeg"), "ffmpeg not installed")
class FfmpegTest(unittest.TestCase):
    def test_extract_all_makes_hook_frames_keyframes_and_audio(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "v.mp4")
            subprocess.run([media.ffmpeg_binary(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=15:duration=8",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-shortest", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", video], check=True)
            wav = os.path.join(directory, "a.wav")
            out = media.extract_all(video, os.path.join(directory, "frames"), os.path.join(directory, "audio.opus"), wav)
            self.assertEqual([f["id"] for f in out["hook_frames"]], ["H0", "H1", "H2", "H3"])
            self.assertTrue(1 <= len(out["keyframes"]) <= 8)
            self.assertTrue(all(os.path.getsize(f["path"]) > 0 for f in out["hook_frames"] + out["keyframes"]))
            self.assertTrue(os.path.getsize(os.path.join(directory, "audio.opus")) > 0)
            self.assertTrue(os.path.exists(wav))
            self.assertAlmostEqual(out["duration"], 8.0, delta=0.5)


class TranscriptTest(unittest.TestCase):
    def test_music_guesses_are_not_speech_but_confident_hindi_is(self):
        shaped = transcribe.shape({"language": "hi", "segments": [
            {"start": 0, "end": 5, "text": "L", "no_speech_prob": 0.0, "avg_logprob": -2.8, "compression_ratio": 0.4},
            # Devanagari compresses well: a 2.45 ratio with a -0.12 logprob is real speech.
            {"start": 5, "end": 10, "text": "अगर कोई प्रॉपर्टी खरीद रहे हो", "no_speech_prob": 0.0, "avg_logprob": -0.12, "compression_ratio": 2.45},
            {"start": 10, "end": 12, "text": "la la la la la la", "no_speech_prob": 0.1, "avg_logprob": -0.6, "compression_ratio": 3.5},
        ]})
        self.assertEqual([s["speech"] for s in shaped["segments"]], [False, True, False])
        self.assertTrue(shaped["speech_detected"])
        self.assertEqual(shaped["text"], "अगर कोई प्रॉपर्टी खरीद रहे हो")


class SelectionTest(Base):
    def test_selection_covers_recent_top_sponsored_and_collab(self):
        posts = self.reel_posts("cook", likes={"DAAAAAAAAA1": 5000, "DAAAAAAAAA2": 4000})
        posts[2]["caption_text"] = "#ad Tide gel"
        posts[3]["permalink"] = "https://www.instagram.com/friend/reel/DAAAAAAAAA4/"
        posts.append({"permalink": "https://www.instagram.com/cook/p/DXAAAAAAAA1/", "platform_content_id": "DXAAAAAAAA1",
                      "content_type": "post", "like_count": 1})
        self.capture("cook", posts)
        connection = sqlite3.connect(self.database)
        chosen = {item["shortcode"]: item["reasons"] for item in selection.select_reels(connection, "cook")}
        connection.close()
        self.assertEqual(len(chosen), selection.TARGET)
        newest = sorted(CODES, key=media.shortcode_to_pk, reverse=True)[: selection.RECENT]
        self.assertTrue(all("recent" in chosen[code] for code in newest))
        self.assertIn("top_performer", chosen["DAAAAAAAAA1"])
        self.assertIn("sponsored", chosen["DAAAAAAAAA3"])
        self.assertIn("collab", chosen["DAAAAAAAAA4"])
        self.assertNotIn("DXAAAAAAAA1", chosen)

    def test_follow_ups_sit_below_scrape_priorities(self):
        self.capture("cook", self.reel_posts("cook", count=5))
        follow_ups = selection.reel_follow_ups(self.database, "cook", brand_ready=True)
        self.assertEqual(len(follow_ups), 5)
        self.assertTrue(all(f.kind == "media.reel" and f.priority < 0 for f in follow_ups))
        self.assertEqual(follow_ups[0].payload["username"], "cook")
        plain = selection.reel_follow_ups(self.database, "cook", brand_ready=False)
        self.assertLess(plain[0].priority, follow_ups[0].priority)


class ScrapeFollowUpTest(Base):
    def test_scrape_adds_reel_follow_ups_and_never_fails_on_selection_errors(self):
        from microindia_scraper.handlers.scraping import _reel_follow_ups

        self.capture("cook", self.reel_posts("cook", count=3))
        ctx = TaskContext(page=None, browser=None, database=self.database, tasks=None)
        self.assertEqual(len(_reel_follow_ups(ctx, "cook", True)), 3)
        broken = TaskContext(page=None, browser=None, database=os.path.join(self.tmp.name, "missing", "x.sqlite3"), tasks=None)
        self.assertEqual(_reel_follow_ups(broken, "cook", False), [])


class FakePage:
    """A signed-in tab whose fetch returns canned media JSON."""

    def __init__(self, response):
        self.url = "https://www.instagram.com/"
        self.response = response
        self.fetched = []

    async def get_url(self):
        return self.url

    async def goto(self, url):
        self.url = url

    async def evaluate(self, script, *args):
        self.fetched.append(script)
        return json.dumps(self.response)


class MediaReelHandlerTest(Base):
    def run_handler(self, response, key="DdMJKW6SQGE"):
        tasks = TaskStore(self.database)
        page = FakePage(response)
        context = TaskContext(page=page, browser=None, database=self.database, tasks=tasks)
        task = {"task_id": 1, "kind": "media.reel", "key": key, "priority": -6,
                "payload": {"shortcode": key, "username": "mummastessa"}}

        def fake_download(url, path, **_):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"mp4")
            return 3

        with mock.patch.object(fetch, "download", side_effect=fake_download):
            result = asyncio.run(get_handler("media.reel").fn(context, task))
        tasks.close()
        return result, page

    def test_media_json_is_stored_video_downloaded_and_analysis_queued(self):
        result, page = self.run_handler({"status": 200, "url": "u", "data": {"items": [MEDIA_ITEM], "status": "ok"}, "text": ""})
        self.assertIsInstance(result, Done)
        self.assertEqual([(f.kind, f.key, f.priority) for f in result.follow_ups], [("analyze.reel", "DdMJKW6SQGE", -6)])
        self.assertIn("/api/v1/media/3984600064529596804/info/", page.fetched[-1])
        connection = db.connect(self.database)
        row = db.latest_media(connection, "DdMJKW6SQGE")
        connection.close()
        self.assertEqual(row["play_count"], 6508)
        self.assertEqual(row["audio_json"]["artist"], "rasikumargalle")
        self.assertEqual(row["usertags"], ["tide.india"])
        self.assertIsNone(row["view_count"])  # unknown stays null
        self.assertNotIn("video_dash_manifest", row["raw_json"])
        self.assertTrue(os.path.exists(media.paths_for(self.database, "DdMJKW6SQGE")["video"]))

    def test_missing_media_is_skipped(self):
        result, _ = self.run_handler({"status": 404, "url": "u", "data": {"message": "Media not found", "status": "fail"}, "text": ""})
        self.assertIsInstance(result, Skip)


class AnalyzeReelTest(Base):
    def test_analysis_is_saved_cached_and_checked(self):
        self.capture("cook", self.reel_posts("cook", count=4))
        self.cached_assets("DAAAAAAAAA1")
        with mock.patch("microindia_scraper.llm._execute", return_value=claude_output(reel_result())) as run:
            first = analyze(self.database, "DAAAAAAAAA1")
            second = analyze(self.database, "DAAAAAAAAA1")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(first["meta"]["cached"])
        self.assertTrue(second["meta"]["cached"])
        self.assertEqual(first["meta"]["analysis_version"], load("reel").version)
        self.assertEqual(first["meta"]["evidence_check"]["invalid"], ["K9"])  # the invented frame is caught
        prompt = json.loads(run.call_args.kwargs["input"])["message"]["content"][-1]["text"]
        self.assertIn("[T0-2] [speech] आज दाल बनाते हैं", prompt)
        self.assertIn('"creator_median_likes_plus_comments": 105', prompt)
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        connection = db.connect(self.database)
        rows = connection.execute("SELECT shortcode, creator, model, prompt_hash, input_hash FROM reel_analyses").fetchall()
        connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(tuple(rows[0])[:3], ("DAAAAAAAAA1", "cook", "claude-sonnet-5-5"))

    def test_dossier_is_queued_after_three_reels_and_built_with_opus(self):
        self.capture("cook", self.reel_posts("cook", count=4))
        for code in CODES[:3]:
            self.cached_assets(code)
        with mock.patch("microindia_scraper.llm._execute", side_effect=[claude_output(reel_result(brand="Tide" if i == 0 else None)) for i in range(3)]):
            for code in CODES[:2]:
                analyze(self.database, code)
            self.assertIsNone(dossier_follow_up(self.database, "cook"))
            analyze(self.database, CODES[2])
        follow = dossier_follow_up(self.database, "cook")
        self.assertEqual(follow.kind, "analyze.creator")
        self.assertTrue(follow.key.startswith("cook:"))
        dossier = {"brand_summary": "x", "content_pillars": [{"name": "dal", "description": "d", "reels": 3, "evidence_reels": CODES[:3]}],
                   "signature_formats": [], "voice": {"description": "", "tone": [], "languages": [], "face_on_camera": "usually"},
                   "audience_persona": {"description": "", "evidence_reels": ["NOPE"]},
                   "production_level": {"score": 3, "description": "", "evidence_reels": []},
                   "brands": {"worked_with": [], "sponsored_vs_organic": "", "brand_readiness": ""}, "category_fit": [],
                   "brand_safety": {"rating": "safe", "summary": "", "flags": []}, "strengths": [], "risks": [],
                   "pitch_angles": [], "unknowns": []}
        with mock.patch("microindia_scraper.llm._execute", return_value=claude_output(dossier, stream=False)) as run:
            built = build_dossier(self.database, "cook")
            again = build_dossier(self.database, "cook")
        self.assertEqual(run.call_count, 1)
        self.assertTrue(again["meta"]["cached"])
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(built["meta"]["evidence_check"]["invalid"], ["NOPE"])
        self.assertIsNone(dossier_follow_up(self.database, "cook"))  # this exact set has a dossier now

    def test_handler_fails_cleanly_without_media(self):
        tasks = TaskStore(self.database)
        context = TaskContext(page=None, browser=None, database=self.database, tasks=tasks)
        result = asyncio.run(get_handler("analyze.reel").fn(context, {"task_id": 1, "key": "DAAAAAAAAA1", "payload": {}}))
        tasks.close()
        self.assertEqual(type(result).__name__, "Fail")


class EvidenceAndCadenceTest(unittest.TestCase):
    def test_evidence_problems_flags_refs_that_point_at_nothing(self):
        assets = {"hook_frames_json": [{"id": "H0"}], "frames_json": [{"id": "K1"}]}
        transcript = {"segments": [{"start": 0, "end": 5}]}
        check = evidence_problems({"a": {"evidence": ["H0", "K1", "K4", "T2-4", "T90", "caption", "frame 3"]}}, assets, transcript, 10.0)
        self.assertEqual(check["invalid"], ["K4", "T90", "frame 3"])
        self.assertEqual(dossier_problems({"x": {"evidence_reels": ["A", "B"]}}, ["A"])["invalid"], ["B"])

    def test_cadence_from_media_ids_ignores_missing_dates(self):
        posts = [{"permalink": f"https://www.instagram.com/x/reel/{code}/"} for code in ("Dd_AFsYydDi", "DdVsy2Isvzk", "Dd1QLnXPcuN", "DdrN-J5PjLE")]
        cadence = cadence_from_ids(posts, now=1791000000)
        self.assertEqual(cadence["posts_considered"], 4)
        self.assertGreater(cadence["posts_per_week"], 0.5)


class SchemaTest(unittest.TestCase):
    def test_prompts_are_versioned_and_schemas_use_the_niche_taxonomy(self):
        reel, creator = load("reel"), load("creator")
        self.assertTrue(reel.version.startswith("reel-v"))
        self.assertTrue(creator.version.startswith("creator-v"))
        self.assertEqual(reel.schema["properties"]["topic"]["properties"]["niche"]["enum"], list(NICHES) + [None])
        self.assertEqual(creator.schema["properties"]["category_fit"]["items"]["properties"]["niche"]["enum"], list(NICHES))

    def test_task_kinds_are_registered(self):
        for kind in ("media.reel", "analyze.reel", "analyze.creator"):
            self.assertIn(kind, registered_kinds())
        self.assertTrue(get_handler("media.reel").needs_page)
        self.assertFalse(get_handler("analyze.reel").needs_page)
        self.assertFalse(get_handler("analyze.creator").needs_page)


class EvalTest(Base):
    def test_scoring_and_comparison_with_previous_run(self):
        self.assertEqual(evals.score_field("Product_Demo", "product demo"), 1.0)
        self.assertEqual(evals.score_field(3, 4), 1.0)
        self.assertEqual(evals.score_field(True, False), 0.0)
        self.assertEqual(evals.score_field(["Hindi", "English"], [{"language": "Hindi"}]), 0.667)
        self.assertEqual(evals.score_field({"any_of": ["food", "baking"]}, "baking"), 1.0)
        self.assertEqual(evals.get_path({"category_fit": [{"niche": "food"}, {"niche": "travel"}]}, "category_fit.top"), "food")

        missing = evals.run(self.database, "reels")
        self.assertEqual(missing["sets"]["reels"]["status"], "no_golden_set")

        self.capture("cook", self.reel_posts("cook", count=3))
        self.cached_assets("DAAAAAAAAA1")
        with mock.patch("microindia_scraper.llm._execute", return_value=claude_output(reel_result())):
            analyze(self.database, "DAAAAAAAAA1")
        golden = os.path.join(evals.evals_dir(self.database), "golden_reels.json")
        with open(golden, "w") as handle:
            json.dump({"items": [{"shortcode": "DAAAAAAAAA1", "expected": {"topic.niche": "food", "format.primary": "recipe"}}]}, handle)
        first = evals.run(self.database, "reels", run_missing=False)
        self.assertEqual(first["sets"]["reels"]["overall"], 100.0)
        self.assertEqual(first["intrinsic"]["reel_invalid_evidence_rate"], round(1 / 13, 4))  # K9 of 13 refs
        with open(golden, "w") as handle:
            json.dump({"items": [{"shortcode": "DAAAAAAAAA1", "expected": {"topic.niche": "travel", "format.primary": "recipe"}}]}, handle)
        second = evals.run(self.database, "reels", run_missing=False)
        regression = second["comparison"]["sets"]["reels"]["regressions"]
        self.assertEqual(regression[0]["field"], "topic.niche")
        self.assertEqual(regression[0]["examples"][0]["actual"], "food")
        self.assertTrue(os.path.exists(second["written_to"]))


# -- review fixes: backpressure, cleanup, dossier triggers, error mapping -------------------------------

from microindia_scraper import llm  # noqa: E402
from microindia_scraper.analysis import ops  # noqa: E402
from microindia_scraper.analysis.creator import dossier_status  # noqa: E402
from microindia_scraper.runtime import Retry, Fail  # noqa: E402


def minimal_dossier(codes):
    return {"brand_summary": "x", "content_pillars": [], "signature_formats": [],
            "voice": {"description": "", "tone": [], "languages": [], "face_on_camera": "usually"},
            "audience_persona": {"description": "", "evidence_reels": list(codes)},
            "production_level": {"score": 3, "description": "", "evidence_reels": []},
            "brands": {"worked_with": [], "sponsored_vs_organic": "", "brand_readiness": ""}, "category_fit": [],
            "brand_safety": {"rating": "safe", "summary": "", "flags": []}, "strengths": [], "risks": [],
            "pitch_angles": [], "unknowns": []}


class Pipeline(Base):
    def analysed(self, codes, creator="cook"):
        for code in codes:
            self.cached_assets(code, creator=creator)
        with mock.patch("microindia_scraper.llm._execute", side_effect=[claude_output(reel_result()) for _ in codes]):
            for code in codes:
                analyze(self.database, code)

    def handler_run(self, kind, task, tasks=None):
        own = tasks is None
        tasks = tasks or TaskStore(self.database)
        context = TaskContext(page=None, browser=None, database=self.database, tasks=tasks)
        try:
            return asyncio.run(get_handler(kind).fn(context, task))
        finally:
            if own:
                tasks.close()


class DossierTriggerTest(Pipeline):
    def test_last_two_reels_finishing_together_are_caught_by_the_scheduler(self):
        self.capture("cook", self.reel_posts("cook", count=4))
        self.analysed(CODES[:2])
        tasks = TaskStore(self.database)
        for code in CODES[2:4]:
            tasks.enqueue("analyze.reel", code, {"shortcode": code, "username": "cook"}, priority=-6)
        first, second = tasks.lease("a", ["analyze.reel"]), tasks.lease("b", ["analyze.reel"])
        for task in (first, second):
            self.cached_assets(task["key"])
        with mock.patch("microindia_scraper.llm._execute", side_effect=[claude_output(reel_result()) for _ in range(2)]):
            results = [self.handler_run("analyze.reel", task, tasks) for task in (first, second)]
        # Each saw its sibling still leased: the fast path queued nothing.
        self.assertEqual([r.follow_ups for r in results], [[], []])
        for task, result in zip((first, second), results):
            tasks.apply(task, "a" if task is first else "b", result)
        self.assertEqual(ops.queue_due_dossiers(tasks), 1)
        row = tasks.connection.execute("SELECT key, priority FROM tasks WHERE kind = 'analyze.creator'").fetchone()
        self.assertTrue(row["key"].startswith("cook:"))
        self.assertEqual(row["priority"], -5)  # reel priority + 1
        self.assertEqual(ops.queue_due_dossiers(tasks), 0)  # already queued
        tasks.close()

    def test_scheduler_waits_for_pending_reels_and_ignores_skipped_ones(self):
        self.capture("cook", self.reel_posts("cook", count=5))
        self.analysed(CODES[:3])
        tasks = TaskStore(self.database)
        tasks.enqueue("media.reel", CODES[4], {"shortcode": CODES[4], "username": "cook"})
        self.assertEqual(ops.queue_due_dossiers(tasks), 0)  # a reel is still on its way
        task = tasks.lease("m", ["media.reel"])
        tasks.apply(task, "m", Skip("media not available"))  # the trailing reel was skipped
        self.assertEqual(ops.queue_due_dossiers(tasks), 1)
        tasks.close()

    def test_fast_path_priority_is_reel_priority_plus_one(self):
        self.capture("cook", self.reel_posts("cook", count=3))
        self.analysed(CODES[:3])
        self.assertEqual(dossier_follow_up(self.database, "cook", priority=-6).priority, -5)

    def test_redossier_needs_three_new_analyses_or_a_week(self):
        self.capture("cook", self.reel_posts("cook", count=8))
        self.analysed(CODES[:3])
        with mock.patch("microindia_scraper.llm._execute", return_value=claude_output(minimal_dossier(CODES[:3]), stream=False)):
            build_dossier(self.database, "cook")
        connection = db.connect(self.database)
        self.assertFalse(dossier_status(connection, "cook")["due"])
        connection.close()
        self.analysed(CODES[3:4])
        connection = db.connect(self.database)
        status = dossier_status(connection, "cook")
        self.assertFalse(status["due"])  # one new analysis, dossier is fresh: a daily re-scrape doesn't re-run Opus
        self.assertTrue(dossier_status(connection, "cook", now=__import__("time").time() + 8 * 86400)["due"])
        connection.close()
        with mock.patch("microindia_scraper.llm._execute") as never:
            cached = build_dossier(self.database, "cook")
        never.assert_not_called()
        self.assertTrue(cached["meta"]["cached"])
        self.analysed(CODES[4:6])
        connection = db.connect(self.database)
        self.assertTrue(dossier_status(connection, "cook")["due"])  # three new analyses
        connection.close()


class HandlerResultMappingTest(Pipeline):
    def reel_task(self, attempts=1):
        return {"task_id": 7, "kind": "analyze.reel", "key": CODES[0], "priority": -6, "attempts": attempts,
                "payload": {"shortcode": CODES[0], "username": "cook"}}

    def test_claude_unavailable_is_a_refunded_retry_much_later(self):
        self.capture("cook", self.reel_posts("cook", count=3))
        self.cached_assets(CODES[0])
        limit = mock.Mock(returncode=1, stdout=json.dumps({"type": "result", "is_error": True, "subtype": "success",
                                                           "result": "Claude AI usage limit reached"}), stderr="")
        task = self.reel_task(attempts=2)
        with mock.patch("microindia_scraper.llm._execute", return_value=limit):
            result = self.handler_run("analyze.reel", task)
        self.assertIsInstance(result, Retry)
        self.assertGreaterEqual(result.after_seconds, 900)
        self.assertLessEqual(result.after_seconds, 2400)
        self.assertEqual(task["attempts"], 1)  # given back
        self.assertGreater(llm.cooldown_remaining(self.database), 0)  # and every other call now fails fast

    def test_model_error_is_a_counted_retry_and_missing_input_fails_and_cleans_up(self):
        self.capture("cook", self.reel_posts("cook", count=3))
        self.cached_assets(CODES[0])
        broken = mock.Mock(returncode=1, stdout=json.dumps({"type": "result", "is_error": True, "subtype": "error_during_execution",
                                                            "result": "Tool StructuredOutput failed validation"}), stderr="")
        task = self.reel_task(attempts=1)
        with mock.patch("microindia_scraper.llm._execute", return_value=broken):
            result = self.handler_run("analyze.reel", task)
        self.assertIsInstance(result, Retry)
        self.assertEqual(result.after_seconds, 600)
        self.assertEqual(task["attempts"], 1)
        orphan = media.paths_for(self.database, CODES[5])["video"]
        os.makedirs(os.path.dirname(orphan), exist_ok=True)
        open(orphan, "wb").close()
        missing = {**self.reel_task(), "key": CODES[5], "payload": {"shortcode": CODES[5]}}
        self.assertIsInstance(self.handler_run("analyze.reel", missing), Fail)
        self.assertFalse(os.path.exists(orphan))

    def test_broken_video_fails_on_the_last_attempt_and_deletes_the_mp4(self):
        connection = db.connect(self.database)
        db.save_media(connection, {"shortcode": CODES[0], "creator": "cook", "duration": 5.0})
        connection.close()
        video = media.paths_for(self.database, CODES[0])["video"]
        os.makedirs(os.path.dirname(video), exist_ok=True)
        with open(video, "wb") as handle:
            handle.write(b"not an mp4")
        with mock.patch.object(media, "extract_all", side_effect=RuntimeError("ffmpeg failed")):
            early = self.handler_run("analyze.reel", self.reel_task(attempts=1))
            self.assertIsInstance(early, Retry)
            self.assertTrue(os.path.exists(video))
            last = self.handler_run("analyze.reel", self.reel_task(attempts=3))
        self.assertIsInstance(last, Fail)
        self.assertFalse(os.path.exists(video))

    def test_creator_unavailable_is_refunded(self):
        self.capture("cook", self.reel_posts("cook", count=3))
        self.analysed(CODES[:3])
        llm.start_cooldown(self.database, 1200)
        task = {"task_id": 9, "kind": "analyze.creator", "key": "cook:x", "attempts": 2, "payload": {"username": "cook"}}
        result = self.handler_run("analyze.creator", task)
        self.assertIsInstance(result, Retry)
        self.assertGreaterEqual(result.after_seconds, 1200)
        self.assertEqual(task["attempts"], 1)


class Mp4CleanupTest(Pipeline):
    def test_mp4_is_deleted_once_assets_are_saved_even_if_the_model_fails(self):
        connection = db.connect(self.database)
        db.save_media(connection, {"shortcode": CODES[0], "creator": "cook", "duration": 5.0})
        connection.close()
        video = media.paths_for(self.database, CODES[0])["video"]
        os.makedirs(os.path.dirname(video), exist_ok=True)
        open(video, "wb").close()
        frames = media.paths_for(self.database, CODES[0])["frames"]

        def fake_extract(video_path, frames_dir, opus, wav):
            os.makedirs(frames_dir, exist_ok=True)
            path = os.path.join(frames_dir, "H0.jpg")
            open(path, "wb").write(b"jpg")
            return {"duration": 5.0, "width": 1, "height": 1, "has_audio": False, "scene_changes": [],
                    "hook_frames": [{"id": "H0", "t": 0.0, "path": path}], "keyframes": [{"id": "K1", "t": 3.0, "path": path}],
                    "seconds": {"frames": 0.1, "audio": 0.0}}

        broken = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(media, "extract_all", side_effect=fake_extract), \
                mock.patch("microindia_scraper.llm._execute", return_value=broken):
            with self.assertRaises(llm.LLMError):
                analyze(self.database, CODES[0])
        self.assertFalse(os.path.exists(video))
        self.assertTrue(os.path.exists(os.path.join(frames, "H0.jpg")))  # re-analysis needs no video

    def test_failed_download_leaves_no_part_file(self):
        class Dying:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __init__(self):
                self.calls = 0

            def read(self, size):
                self.calls += 1
                if self.calls > 1:
                    raise OSError("connection reset")
                return b"x" * 10

        path = os.path.join(self.tmp.name, "v", "a.mp4")
        with mock.patch("urllib.request.urlopen", return_value=Dying()):
            with self.assertRaises(OSError):
                fetch.download("https://cdn.example/v.mp4", path)
        self.assertEqual(os.listdir(os.path.dirname(path)), [])

    def test_sweep_removes_orphans_only(self):
        tasks = TaskStore(self.database)
        directory = os.path.join(media.media_root(self.database), "video")
        os.makedirs(directory, exist_ok=True)
        old = __import__("time").time() - 2 * 86400
        for name in ("orphan.mp4", "waiting.mp4", "fresh.mp4", "stale.mp4.part"):
            open(os.path.join(directory, name), "wb").close()
            if name != "fresh.mp4":
                os.utime(os.path.join(directory, name), (old, old))
        tasks.enqueue("analyze.reel", "waiting", {"shortcode": "waiting"})
        self.assertEqual(ops.sweep_videos(tasks), 2)
        self.assertEqual(sorted(os.listdir(directory)), ["fresh.mp4", "waiting.mp4"])
        tasks.close()


class MediaBackpressureTest(Base):
    def test_gate_holds_media_while_the_analyzer_is_behind_or_disk_is_short(self):
        from microindia_scraper.run import _gate_for, _schedulers_for

        tasks = TaskStore(self.database)
        gate = _gate_for(["media.reel"], 3000, self.database)
        ops._HOLD_CACHE.clear()
        self.assertEqual(gate(tasks), {})
        for index in range(ops.MEDIA_ANALYZE_BACKLOG + 1):
            tasks.enqueue("analyze.reel", f"r{index}")
        ops._HOLD_CACHE.clear()
        self.assertEqual(gate(tasks)["media.reel"], ops.HOLD_PRIORITY)
        tasks.enqueue("media.reel", "m1", {"shortcode": "DAAAAAAAAA1"}, priority=-6)
        self.assertIsNone(tasks.lease("media", ["media.reel"], priority_floor=gate(tasks)))
        tasks.connection.execute("DELETE FROM tasks WHERE kind = 'analyze.reel'")
        tasks.connection.commit()
        ops._HOLD_CACHE.clear()
        with mock.patch.object(ops, "free_bytes", return_value=5 * 1024 ** 3):
            self.assertIn("free on disk", ops.media_hold_reason(tasks, self.database, cache_seconds=0))
        with mock.patch.object(ops, "video_bytes", return_value=3 * 1024 ** 3):
            self.assertIn("data/media/video", ops.media_hold_reason(tasks, self.database, cache_seconds=0))
        ops._HOLD_CACHE.clear()
        names = [fn.__name__ for fn in _schedulers_for(["analyze.reel", "analyze.creator"])]
        self.assertEqual(names, ["queue_due_dossiers", "sweep_videos"])
        tasks.close()

    def test_handler_refunds_when_held(self):
        tasks = TaskStore(self.database)
        for index in range(ops.MEDIA_ANALYZE_BACKLOG + 1):
            tasks.enqueue("analyze.reel", f"r{index}")
        ops._HOLD_CACHE.clear()
        task = {"task_id": 1, "kind": "media.reel", "key": "DdMJKW6SQGE", "attempts": 1, "payload": {"shortcode": "DdMJKW6SQGE"}}
        context = TaskContext(page=None, browser=None, database=self.database, tasks=tasks)
        result = asyncio.run(get_handler("media.reel").fn(context, task))
        tasks.close()
        ops._HOLD_CACHE.clear()
        self.assertIsInstance(result, Retry)
        self.assertEqual(task["attempts"], 0)

    def test_follow_ups_are_fifo_within_priority_and_brand_ready_first(self):
        self.capture("cook", self.reel_posts("cook", count=3))
        plain = selection.reel_follow_ups(self.database, "cook", now=1_790_000_000)
        self.assertEqual({f.priority for f in plain}, {selection.MEDIA_PRIORITY})
        # FIFO: due now, never mirrored into the past (that starved retried tasks behind the backlog).
        self.assertEqual({f.delay_seconds for f in plain}, {0.0})
        brand = selection.reel_follow_ups(self.database, "cook", brand_ready=True)
        self.assertGreater(brand[0].priority, plain[0].priority)


class FetchClassificationTest(Base):
    def test_errors_map_to_skip_retry_or_refunded_backoff(self):
        cases = [
            ({"status": 404, "url": "u", "data": None, "text": "Not Found"}, Skip, None),
            ({"status": 400, "url": "u", "data": {"message": "Media not found or unavailable", "status": "fail"}, "text": ""}, Skip, None),
            ({"status": 500, "url": "u", "data": None, "text": "Internal error"}, Retry, "counted"),
            ({"status": 0, "url": "u", "data": None, "text": "TypeError: Failed to fetch"}, Retry, "counted"),
            ({"status": 400, "url": "u", "data": {"message": "Please wait a few minutes before you try again.", "status": "fail"}, "text": ""}, Retry, "refunded"),
            ({"status": 400, "url": "u", "data": {"message": "feedback_required", "spam": True, "status": "fail"}, "text": ""}, Retry, "refunded"),
            ({"status": 403, "url": "u", "data": {"message": "something else", "status": "fail"}, "text": ""}, Retry, "counted"),
        ]
        for response, expected, attempt_kind in cases:
            ops._HOLD_CACHE.clear()
            tasks = TaskStore(self.database)
            context = TaskContext(page=FakePage(response), browser=None, database=self.database, tasks=tasks)
            task = {"task_id": 1, "kind": "media.reel", "key": "DdMJKW6SQGE", "attempts": 2, "priority": -6,
                    "payload": {"shortcode": "DdMJKW6SQGE"}}
            result = asyncio.run(get_handler("media.reel").fn(context, task))
            tasks.close()
            self.assertIsInstance(result, expected, response)
            if attempt_kind:
                self.assertEqual(task["attempts"], 1 if attempt_kind == "refunded" else 2, response)


class SoftLimitPacerTest(unittest.TestCase):
    def test_soft_limit_slows_the_shared_pacer(self):
        import asyncio
        from unittest import mock
        from microindia_scraper.analysis import fetch
        from microindia_scraper.runtime.pacing import Throttled

        recorded = []

        class Pacer:
            def record(self, status):
                recorded.append(status)

        class Page:
            pacer = Pacer()

        soft = {"status": 400, "data": {"message": "Please wait a few minutes before you try again."}}
        with mock.patch.object(fetch, "fetch_json", mock.AsyncMock(return_value=soft)):
            with self.assertRaises(Throttled):
                asyncio.run(fetch.fetch_reel(Page(), ":memory:", "Dd_AFsYydDi", download_video=False))
        self.assertEqual(recorded, [429])


if __name__ == "__main__":
    unittest.main()
