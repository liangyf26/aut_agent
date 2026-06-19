from __future__ import annotations

from pathlib import Path

from prototype.stage2.app.discovery import DiscoveryArtifactWriter, DiscoveryPlanner
from prototype.stage2.app.runtime.templates import load_template_bundle


def main() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "templates" / "suyuan_online_apply"
    bundle = load_template_bundle(template_dir)
    result = DiscoveryPlanner().plan(
        template_name=bundle.name,
        template=bundle.template,
        baseline=bundle.baseline,
    )
    output_dir = Path(__file__).resolve().parent / "_example_output"
    paths = DiscoveryArtifactWriter(output_dir).write(result)
    for key, path in paths.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    main()
