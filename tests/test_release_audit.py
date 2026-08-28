from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "check_public_history.py"


class PublicHistoryAuditTest(unittest.TestCase):
    def _repository(self, root: Path) -> tuple[Path, dict[str, str]]:
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", repository], check=True)
        environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Example Contributor",
            "GIT_AUTHOR_EMAIL": "contributor@example.com",
            "GIT_COMMITTER_NAME": "Example Contributor",
            "GIT_COMMITTER_EMAIL": "contributor@example.com",
        }
        return repository, environment

    def _commit(self, repository: Path, environment: dict[str, str], message: str) -> None:
        subprocess.run(["git", "-C", repository, "add", "."], check=True)
        subprocess.run(
            ["git", "-C", repository, "commit", "-q", "-m", message],
            env=environment,
            check=True,
        )

    def test_clean_signed_off_root_is_valid_initial_public_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, environment = self._repository(Path(temp))
            repository.joinpath("README.md").write_text("# Public project\n", encoding="utf-8")
            self._commit(
                repository,
                environment,
                "feat: public release\n\nSigned-off-by: Example Contributor <contributor@example.com>",
            )

            completed = subprocess.run(
                [str(AUDIT), "--repository", str(repository), "--initial-release"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("public history audit passed (1 commits)", completed.stdout)

    def test_old_unsigned_or_sensitive_history_blocks_public_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, environment = self._repository(Path(temp))
            repository.joinpath("README.md").write_text("# Private draft\n", encoding="utf-8")
            self._commit(repository, environment, "chore: private draft")
            repository.joinpath("identity.pem").write_text(
                "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nnot-a-real-key\n",
                encoding="utf-8",
            )
            self._commit(
                repository,
                environment,
                "feat: candidate\n\nSigned-off-by: Example Contributor <contributor@example.com>",
            )

            completed = subprocess.run(
                [str(AUDIT), "--repository", str(repository), "--initial-release"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("exactly one reviewed clean-root commit", completed.stderr)
        self.assertIn("missing a valid DCO", completed.stderr)
        self.assertIn("possible private key", completed.stderr)
        self.assertIn("sensitive runtime/artifact path", completed.stderr)


if __name__ == "__main__":
    unittest.main()
