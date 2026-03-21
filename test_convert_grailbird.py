import tempfile
import unittest
from pathlib import Path

from convert_grailbird import parse_user_details


class ParseUserDetailsTests(unittest.TestCase):
    def parse_fixture(self, content):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "user_details.js"
            path.write_text(content, encoding="utf-8")
            return parse_user_details(path)

    def test_parses_current_archive_format(self):
        result = self.parse_fixture(
            """var user_details =  {
  "expanded_url" : "http:\\/\\/whatevernevermind.com",
  "screen_name" : "bradbarrish",
  "full_name" : "Brad Barrish",
  "id" : "926",
  "created_at" : "2006-07-14 18:42:26 +0000"
}"""
        )

        self.assertEqual(
            result,
            {
                "id": "926",
                "screen_name": "bradbarrish",
                "full_name": "Brad Barrish",
                "created_at": "2006-07-14 18:42:26 +0000",
            },
        )

    def test_parses_minified_reordered_object(self):
        result = self.parse_fixture(
            'var user_details={"created_at":"2006-07-14 18:42:26 +0000","id":926,"full_name":"Brad Barrish","screen_name":"bradbarrish"};'
        )

        self.assertEqual(result["id"], "926")
        self.assertEqual(result["screen_name"], "bradbarrish")
        self.assertEqual(result["full_name"], "Brad Barrish")

    def test_ignores_trailing_comma(self):
        result = self.parse_fixture(
            """var user_details = {
  "screen_name": "bradbarrish",
  "id": "926",
  "full_name": "Brad Barrish",
  "created_at": "2006-07-14 18:42:26 +0000",
};"""
        )

        self.assertEqual(result["created_at"], "2006-07-14 18:42:26 +0000")


if __name__ == "__main__":
    unittest.main()
