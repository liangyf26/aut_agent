from __future__ import annotations

from pathlib import Path

from .models import DiscoveryResult


class DiscoveryArtifactWriter:
    """Persists discovery artifacts under a caller-provided output root."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, result: DiscoveryResult) -> dict[str, Path]:
        page_entries_payload = self._collection_payload(
            template_name=result.template_name,
            generated_at=result.generated_at,
            strategy=result.strategy,
            review_hints=result.review_hints,
            stats=result.stats,
            notes=result.notes,
            items=[item.to_dict() for item in result.page_entries],
        )
        feature_points_payload = self._collection_payload(
            template_name=result.template_name,
            generated_at=result.generated_at,
            strategy=result.strategy,
            review_hints=result.review_hints,
            stats=result.stats,
            notes=result.notes,
            items=[item.to_dict() for item in result.feature_points],
        )
        screenshot_payload = self._collection_payload(
            template_name=result.template_name,
            generated_at=result.generated_at,
            strategy=result.strategy,
            review_hints=result.review_hints,
            stats=result.stats,
            notes=result.notes,
            items=[item.to_dict() for item in result.screenshot_records],
        )
        review_queue_payload = self._collection_payload(
            template_name=result.template_name,
            generated_at=result.generated_at,
            strategy=result.strategy,
            review_hints=result.review_hints,
            stats=result.stats,
            notes=result.notes,
            items=list(result.review_queue),
        )

        paths = {
            "page_entries": self.output_dir / "page_entries.json",
            "feature_points": self.output_dir / "feature_points.json",
            "screenshot_records": self.output_dir / "screenshot_records.json",
            "review_queue": self.output_dir / "discovery_review_queue.json",
            "discovery_summary": self.output_dir / "discovery_result.json",
        }
        paths["page_entries"].write_text(self._json(page_entries_payload), encoding="utf-8")
        paths["feature_points"].write_text(self._json(feature_points_payload), encoding="utf-8")
        paths["screenshot_records"].write_text(self._json(screenshot_payload), encoding="utf-8")
        paths["review_queue"].write_text(self._json(review_queue_payload), encoding="utf-8")
        paths["discovery_summary"].write_text(self._json(result.to_dict()), encoding="utf-8")
        return paths

    def _collection_payload(
        self,
        *,
        template_name: str,
        generated_at: str,
        strategy: str,
        review_hints: dict[str, object],
        stats: dict[str, object],
        notes: list[str],
        items: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "template_name": template_name,
            "generated_at": generated_at,
            "strategy": strategy,
            "count": len(items),
            "review_hints": review_hints,
            "stats": stats,
            "notes": notes,
            "items": items,
        }

    def _json(self, payload: dict[str, object]) -> str:
        import json

        return json.dumps(payload, ensure_ascii=False, indent=2)
