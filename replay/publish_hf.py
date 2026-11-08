from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi

CARD = """---
license: mit
pretty_name: Ditto pilot results
configs:
  - config_name: compaction
    data_files: compaction.jsonl
  - config_name: titles
    data_files: titles.jsonl
---

# Ditto pilot results

Replay bench of the Ditto router pilot. Each of the 36 sessions from Woolford's compaction study replication package (MIT) was turned into the compaction request Claude Code sends and passed through a fork of Ditto's router three ways: to Claude Haiku 5.5 and Claude Sonnet 5.5 as the default compaction model, and with the archetype route table on, which sends compaction to Microtensor's certified `mt/ctx_compress`.

`compaction` has one row per session with the session, the three summaries side by side, the arena grader's marks, the cost and the time. `titles` has the title request for each session; the Microtensor title route is a clearly labelled stand in (Qwen2.5 1.5B Instruct), not a certified model.

Code, relay logs and the method: https://github.com/microtensor-io/ditto-pilot
"""


def main() -> int:
    parser = argparse.ArgumentParser(prog="replay-publish-hf")
    parser.add_argument("--folder", default="results/hf")
    parser.add_argument("--repo", default="microtensor-io/ditto-pilot-results")
    args = parser.parse_args()
    folder = Path(args.folder)
    (folder / "README.md").write_text(CARD, encoding="utf-8")
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(args.repo, repo_type="dataset", private=True, exist_ok=True)
    api.upload_folder(
        repo_id=args.repo,
        repo_type="dataset",
        folder_path=str(folder),
        commit_message="Add the replay bench results",
    )
    info = api.dataset_info(args.repo)
    print(args.repo, "private" if info.private else "PUBLIC", info.sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
