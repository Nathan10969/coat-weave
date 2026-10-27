"""Check the independently deployable Pi service's repository layout."""

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
PI_ROOT = ROOT / "service" / "pi-harness"


class PiHarnessLayoutTests(unittest.TestCase):
    def test_service_entrypoints_and_attribution_are_present(self):
        for relative in (
            "src/main.ts",
            "src/pi.ts",
            "src/patent-search/tool_schema.json",
            "backend/src/patent_milvus_tool/server.py",
            "package-lock.json",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "docs/licenses/pi-MIT.txt",
        ):
            with self.subTest(path=relative):
                self.assertTrue((PI_ROOT / relative).is_file(), relative)

    def test_official_sdk_and_existing_retrieval_are_preserved(self):
        package = json.loads((PI_ROOT / "package.json").read_text())
        self.assertEqual(
            package["dependencies"]["@earendil-works/pi-coding-agent"], "0.86.0"
        )
        source = (PI_ROOT / "src/pi.ts").read_text()
        self.assertIn('backend === "kg" ? kgTools : [patentMilvusTool]', source)
        self.assertIn('thinkingLevel: "off"', source)

    def test_local_secrets_state_and_build_artifacts_are_ignored(self):
        paths = [
            "service/pi-harness/.env",
            "service/pi-harness/.local/agent/auth.json",
            "service/pi-harness/node_modules/example/index.js",
            "service/pi-harness/dist/main.js",
        ]
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            input="\n".join(paths) + "\n",
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(set(result.stdout.splitlines()), set(paths), result.stderr)
        example = subprocess.run(
            ["git", "check-ignore", "--no-index", "service/pi-harness/.env.example"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(example.returncode, 1)


if __name__ == "__main__":
    unittest.main()
