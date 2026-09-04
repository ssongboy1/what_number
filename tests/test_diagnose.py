import tempfile
import unittest
from pathlib import Path

from what_number import diagnose


class MaskSecretsTest(unittest.TestCase):
    """보고서를 주고받아도 안전하도록 비밀번호는 반드시 가려야 한다."""

    def test_masks_password(self):
        masked = diagnose._mask_secrets("Server=POS;Uid=sa;Pwd=secret123;")
        self.assertNotIn("secret123", masked)
        self.assertIn("가림", masked)

    def test_masks_regardless_of_spelling_or_case(self):
        for text in ("PASSWORD=abc123", "passwd = abc123", "Pwd=abc123"):
            self.assertNotIn("abc123", diagnose._mask_secrets(text))

    def test_keeps_the_useful_parts(self):
        masked = diagnose._mask_secrets("Server=POSPC\SQLEXPRESS;Database=okpos;Pwd=x;")
        self.assertIn("Server=POSPC", masked)
        self.assertIn("Database=okpos", masked)

    def test_leaves_text_without_passwords_alone(self):
        text = "Server=POS;Database=okpos;"
        self.assertEqual(diagnose._mask_secrets(text), text)


class ConnectionHintsTest(unittest.TestCase):
    def test_finds_connection_string_and_hides_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "db.ini").write_text(
                "[DB]\nConnStr=Provider=SQLOLEDB;Data Source=POS;Database=sale;Uid=sa;Pwd=hunter2;\n",
                encoding="utf-8",
            )
            results = diagnose.connection_hints([folder])
        joined = "\n".join(results)
        self.assertIn("db.ini", joined)
        self.assertNotIn("hunter2", joined)

    def test_reads_cp949_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "설정.ini").write_bytes(
                "; 주문 데이터베이스\nDSN=OKPOS;Server=localhost;Database=pos;\n".encode("cp949")
            )
            results = diagnose.connection_hints([folder])
        self.assertIn("설정.ini", "\n".join(results))

    def test_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "readme.ini").write_text("[본문]\n안녕하세요\n", encoding="utf-8")
            results = diagnose.connection_hints([folder])
        self.assertIn("찾지 못했습니다", results[0])

    def test_missing_folder_does_not_raise(self):
        results = diagnose.connection_hints([Path("Z:/없는폴더")])
        self.assertIn("찾지 못했습니다", results[0])


class LookupsDoNotRaiseTest(unittest.TestCase):
    """조사 기능은 어떤 환경에서도 예외 없이 목록을 돌려줘야 한다."""

    def test_odbc_sources(self):
        self.assertIsInstance(diagnose.odbc_sources(), list)

    def test_sql_server_instances(self):
        self.assertIsInstance(diagnose.sql_server_instances(), list)

    def test_running_programs(self):
        self.assertIsInstance(diagnose.running_programs(), list)

    def test_pos_program_dirs(self):
        self.assertIsInstance(diagnose.pos_program_dirs(), list)


class ReportIncludesNewSectionsTest(unittest.TestCase):
    def test_sections_present(self):
        import os

        if os.name != "nt":
            self.skipTest("윈도우에서만 조사합니다")
        report = diagnose.build_report()
        for title in ("SQL Server", "ODBC 연결 설정", "설정 파일 속 접속 정보"):
            self.assertIn(title, report)


if __name__ == "__main__":
    unittest.main()
