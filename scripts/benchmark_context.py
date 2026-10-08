"""Synthetic context construction probe, runnable against either frozen source tree."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from ragagent.conversations.context import ContextBuilder, estimated_context_tokens
from ragagent.domain.conversation_context import ContextMessage, StructuredMemory
from ragagent.domain.research import MetadataFilter
from ragagent.errors import ApplicationError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = []
    for count in (50, 100):
        for language, query in (("en", "How was it trained?"), ("zh", "它采用什么训练方法？")):
            messages = [
                ContextMessage(
                    id=f"message-{i}",
                    ordinal=i,
                    role="user" if i % 2 == 0 else "assistant",
                    content=(
                        "DANN MNIST method discussion. "
                        if language == "en"
                        else "DANN 在 MNIST 的方法讨论。"
                    )
                    * 40,
                )
                for i in range(count)
            ]
            memories = [
                StructuredMemory(
                    id=f"memory-{i:03}", kind="term", key=f"topic-{i}", content=f"Method-{i} " * 30
                )
                for i in range(99)
            ]
            memories.append(
                StructuredMemory(
                    id="constraint",
                    kind="constraint",
                    content="Methods only",
                    filters=MetadataFilter(sections=["Methods"]),
                )
            )
            samples = []
            for _ in range(15):
                begin = time.perf_counter()
                try:
                    bundle = ContextBuilder().build(query, messages, memories=memories)
                    sample = {
                        "status": "constructed",
                        "estimated_input_tokens": estimated_context_tokens(bundle.payload),
                        "payload_bytes": len(
                            json.dumps(bundle.payload, ensure_ascii=False).encode()
                        ),
                        "selected_memory_count": len(bundle.payload["structured_memory"]),
                        "filters": bundle.filters.model_dump(),
                    }
                except ApplicationError as error:
                    sample = {"status": "failed", "error_code": error.code}
                sample["construction_ms"] = (time.perf_counter() - begin) * 1000
                samples.append(sample)
            fixture = {
                "messages": [
                    {
                        field: getattr(m, field)
                        for field in ("id", "ordinal", "role", "content", "status")
                    }
                    for m in messages
                ],
                "memories": [m.model_dump() for m in memories],
                "query": query,
            }
            cases.append(
                {
                    "messages": count,
                    "stored_memories": 100,
                    "language": language,
                    "fixture_sha256": hashlib.sha256(
                        json.dumps(fixture, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest(),
                    "samples": samples,
                }
            )
    import ragagent.conversations.context as context_module

    source = Path(context_module.__file__)
    commit = subprocess.check_output(
        ["git", "-C", str(source.parent), "rev-parse", "HEAD"], text=True
    ).strip()
    args.output.write_text(
        json.dumps(
            {
                "label": "SYNTHETIC CONTEXT CONSTRUCTION / NO LLM OR SCIENTIFIC QUALITY MEASURED",
                "source_commit": commit,
                "source_identity_note": (
                    "Checkout base commit; measured module identity: context_source_sha256"
                ),
                "context_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "clock": "perf_counter",
                "samples_per_case": 15,
                "cases": cases,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
