from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from modpack_translator.pipeline.runner import (  # noqa: E402
    _failed_item_filename,
    _write_failed_items,
)


class FailedItemsFilenameTests(unittest.TestCase):
    def test_long_windows_target_has_short_stable_filename(self) -> None:
        target_name = (
            "ftbquests__ftbq_snbt__"
            "C_Users_User_curseforge_minecraft_Instances_All_the_Mods_10_"
            "To_the_Sky_ATM10SKY_config_ftbquests_quests_lang_zh_tw_"
            "chapters_very_long_quest_chapter_name_that_keeps_going"
        )

        filename = _failed_item_filename(target_name)

        self.assertEqual(filename, _failed_item_filename(target_name))
        self.assertLessEqual(len(filename), 65)
        self.assertTrue(filename.startswith("ftbquests__ftbq_snbt_"))
        self.assertTrue(filename.endswith(".txt"))

    def test_same_prefix_different_targets_do_not_collide(self) -> None:
        first = "ftbquests__ftbq_snbt__C:/instance/one/quests.snbt"
        second = "ftbquests__ftbq_snbt__C:/instance/two/quests.snbt"

        self.assertNotEqual(
            _failed_item_filename(first),
            _failed_item_filename(second),
        )

    def test_writer_preserves_full_target_name_in_file_contents(self) -> None:
        target_name = (
            "ftbquests__ftbq_snbt__"
            + "C:/Users/User/Downloads/Modpack_Translator-v1.5.3/" * 8
            + "config/ftbquests/quests/lang/zh_tw/chapters/chapter.snbt"
        )
        failed = {
            target_name: {
                "chapter.quest.title": "This sentence could not be translated."
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "Failed Items"
            written = _write_failed_items(failed, output_dir)
            files = list(output_dir.rglob("*.txt"))

            self.assertEqual(written, 1)
            self.assertEqual(len(files), 1)
            self.assertLessEqual(len(files[0].name), 65)
            self.assertIn(target_name, files[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
