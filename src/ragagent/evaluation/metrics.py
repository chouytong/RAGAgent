import math
from collections.abc import Iterable
from statistics import mean


def retrieval_metrics(ranking: list[str], relevant: set[str]) -> dict[str, float]:
    if not relevant:
        raise ValueError("no_relevance_labels")
    ranking = list(dict.fromkeys(ranking))

    def recall(k: int) -> float:
        return len(set(ranking[:k]) & relevant) / len(relevant)

    reciprocal = next((1 / i for i, cid in enumerate(ranking[:10], 1) if cid in relevant), 0.0)
    dcg = sum(1 / math.log2(i + 1) for i, cid in enumerate(ranking[:10], 1) if cid in relevant)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(10, len(relevant)) + 1))
    return {
        "Recall@1": recall(1),
        "Recall@5": recall(5),
        "Recall@10": recall(10),
        "Precision@5": len(set(ranking[:5]) & relevant) / 5,
        "MRR@10": reciprocal,
        "nDCG@10": dcg / ideal,
    }


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def average(rows: Iterable[dict[str, float | None]]) -> dict[str, float | None]:
    rows = list(rows)
    summary: dict[str, float | None] = {}
    if not rows:
        return summary
    for key in rows[0]:
        values: list[float] = []
        for row in rows:
            value = row[key]
            if value is not None:
                values.append(value)
        summary[key] = mean(values) if values else None
    return summary
