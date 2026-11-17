from importlib.util import find_spec
import json
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui


SHEET = Path(__file__).resolve().parents[1] / "sheets-demo" / "flows" / "People Detection"
CAMERA_FIXTURE = """
import cv2
import numpy as np
from pathlib import Path
from time import sleep

class TestCamera:
    def __init__(self, index):
        self.reads = 0

    def isOpened(self):
        return True

    def read(self):
        self.reads += 1
        if self.reads == 1:
            return False, None
        if self.reads == 3:
            while not Path("next-frame").exists():
                sleep(0.01)
            return False, None
        if self.reads >= 5:
            while not Path("disconnect").exists():
                sleep(0.01)
            return False, None
        return True, np.full((240, 320, 3), self.reads * 20, dtype=np.uint8)

    def release(self):
        Path("camera-released").touch()

class TestDetector:
    winSize = (64, 128)

    def setSVMDetector(self, coefficients):
        pass

    def detectMultiScale(self, frame, **kwargs):
        return [(40, 30, 70, 160)] if frame[0, 0, 0] == 40 else [], []

cv2.VideoCapture = TestCamera
cv2.HOGDescriptor = TestDetector
"""


@unittest.skipUnless(find_spec("cv2"), "Install the People Detection sheet requirements.")
class PeopleDetectionTests(unittest.TestCase):
    def test_dashboard_recovers_missing_frames_and_clears_video_after_disconnect(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp)
            sheet = root / "flows" / SHEET.name
            copytree(SHEET, sheet, ignore=ignore_patterns(".workspace", "__pycache__"))
            flow = sheet / "flow.py"
            flow.write_text(CAMERA_FIXTURE + flow.read_text())

            with local_ui(root) as (url, _):
                page.goto(url)
                view = page.frame_locator('iframe[title="People Detection flow"]')
                view.locator("#visualize-workflow").click()
                dashboard = view.frame_locator("#dashboard-preview iframe")
                expect(dashboard.get_by_role("status")).to_have_text("Select Run flow to start the camera.")
                run = view.get_by_role("button", name="Run flow", exact=True)
                run.click()
                expect(dashboard.get_by_role("status")).to_have_text("Camera live")
                camera = dashboard.locator("#camera")
                first = camera.get_attribute("src")
                sample_pixel = """async image => {
                    await image.decode();
                    const canvas = document.createElement("canvas");
                    canvas.width = image.naturalWidth;
                    canvas.height = image.naturalHeight;
                    const context = canvas.getContext("2d");
                    context.drawImage(image, 0, 0);
                    return Array.from(context.getImageData(40, 90, 1, 1).data).slice(0, 3);
                }"""
                red, green, blue = camera.evaluate(sample_pixel)
                self.assertLess(red, 150)
                self.assertGreater(green, 180)
                self.assertGreater(blue, 150)

                # A transient read failure is followed by a frame with no detections.
                (sheet / "next-frame").touch()
                expect(camera).not_to_have_attribute("src", first)
                for channel in camera.evaluate(sample_pixel):
                    self.assertAlmostEqual(channel, 80, delta=2)

                (sheet / "disconnect").touch()
                expect(dashboard.get_by_role("status")).to_have_text("Camera run failed. Open the Run panel for details.")
                expect(camera).to_be_hidden()
                self.assertIsNone(camera.get_attribute("src"))
                self.assertTrue((sheet / "camera-released").exists())
                expect(run).to_be_enabled()
                history = list((sheet / ".workspace" / "runs").glob("*.json"))
                self.assertEqual(len(history), 1)
                self.assertEqual(json.loads(history[0].read_text())["status"], "failed")
                self.assertNotIn(first.split(",", 1)[1], history[0].read_text())
