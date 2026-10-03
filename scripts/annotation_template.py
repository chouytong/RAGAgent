"""Create unannotated forms, never fabricated manual labels."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
parser.add_argument("--count", type=int, default=100)
args = parser.parse_args()
if not 1 <= args.count <= 1000:
    parser.error("count must be between 1 and 1000")
args.output.write_text(
    json.dumps(
        {
            "dataset_id": "replace-with-your-dataset-id",
            "label_source": "unannotated",
            "description": "Fill by human review before evaluation.",
            "cases": [
                {
                    "id": f"q{i:03}",
                    "query": "REPLACE with a real question",
                    "question_type": "fact",
                    "filters": {},
                    "relevant_chunk_ids": [],
                    "relevant_paper_ids": [],
                    "expected_answer": "",
                    "notes": "",
                    "required_aspects": [],
                    "annotated_by": None,
                    "annotated_at": None,
                }
                for i in range(1, args.count + 1)
            ],
        },
        indent=2,
    )
)
