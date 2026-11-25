import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    task_id: str
    prompt: str
    constraints: dict = field(default_factory=dict)
    reference: Path | None = None
    assets: tuple = ()
    metadata: dict = field(default_factory=dict)
    split: str = "interactive"

    def public(self):
        return {"task_id": self.task_id, "prompt": self.prompt, "constraints": self.constraints,
                "assets": [p.name for p in self.assets]}

    def instruction(self):
        return self.prompt + "\n" + json.dumps({"constraints": self.constraints, "assets": self.public()["assets"]})


def dataset(path, split=None):
    source = Path(path).resolve()
    rows = []
    seen = set()
    for line_no, line in enumerate(source.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if split and d.get("split") != split:
                continue
            ident, prompt = d["task_id"], d["prompt"]
            if not isinstance(ident, str) or ident in seen or not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("Invalid or duplicate task")
            seen.add(ident)
            ref = d.get("reference_lottie") or d.get("reference_lottie_path")
            ref = (source.parent / ref).resolve(strict=True) if ref else None
            assets = tuple((source.parent / a).resolve(strict=True) for a in d.get("input_assets", []))
            rows.append(Task(ident, prompt, d.get("constraints", {}), ref, assets,
                             d.get("metadata", {}), d.get("split", "eval")))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise ValueError(f"{source}:{line_no}: {exc}") from exc
    targets = {t.reference for t in rows if t.reference}
    if any(a in targets for t in rows for a in t.assets):
        raise ValueError("A scoring reference is also listed as an input asset")
    if not rows:
        raise ValueError("No matching dataset rows")
    return rows
