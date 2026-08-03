"""CLI for reproducible fake-provider golden evaluation."""

import argparse
from pathlib import Path

from app.evaluation.evaluator import evaluate
from app.evaluation.fake import golden_fake_candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    arguments = parser.parse_args()
    report = evaluate(
        golden_fake_candidate,
        provider="deterministic-fake",
        model="phase5-golden-v1",
    )
    payload = report.model_dump_json(indent=2)
    if arguments.output:
        arguments.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
