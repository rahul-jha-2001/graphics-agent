"""Structural Lottie checks and a deterministic, path-aware reference metric."""
import json
import math


def leaves(value, path=()):
    if isinstance(value, dict) and value:
        for k, v in value.items():
            yield from leaves(v, path + (k,))
    elif isinstance(value, list) and value:
        for i, v in enumerate(value):
            yield from leaves(v, path + (i,))
    else:
        yield path, value


def similarity(a, b):
    left, right = dict(leaves(a)), dict(leaves(b))
    keys = left.keys() | right.keys()
    score = 0.0
    for k in left.keys() & right.keys():
        x, y = left[k], right[k]
        if type(x) in (int, float) and type(y) in (int, float):
            score += 1 - min(1, abs(x-y) / max(1, abs(x), abs(y)))
        elif type(x) is type(y) and x == y:
            score += 1
    return score / len(keys)


def animated(value):
    if isinstance(value, list):
        return any(animated(v) for v in value)
    if not isinstance(value, dict):
        return False
    if value.get("a") == 1 and isinstance(value.get("k"), list):
        keys = [k for k in value["k"] if isinstance(k, dict) and "t" in k]
        values = {json.dumps(k[p], sort_keys=True) for k in keys for p in ("s", "e") if p in k}
        if len({k["t"] for k in keys}) >= 2 and len(values) >= 2:
            return True
    return any(animated(v) for v in value.values())


def grade(doc, constraints, reference=None):
    reasons = []
    for key in ("w", "h", "fr"):
        if type(doc.get(key)) not in (int, float) or not math.isfinite(doc[key]) or doc[key] <= 0:
            reasons.append(f"invalid {key}")
    if not doc.get("v") or doc.get("op", 0) <= doc.get("ip", 0):
        reasons.append("missing version or invalid frame range")
    for name, key in (("width", "w"), ("height", "h"), ("fps", "fr")):
        if name in constraints and not math.isclose(doc.get(key, 0), constraints[name], abs_tol=0.001):
            reasons.append(f"{name} constraint failed")
    if "duration" in constraints and doc.get("fr", 0) > 0:
        if abs((doc["op"]-doc["ip"])/doc["fr"] - constraints["duration"]) > 0.5/doc["fr"]:
            reasons.append("duration constraint failed")
    assets = {a.get("id"): a for a in doc.get("assets", [])}

    def content(layer, visited):
        if layer.get("hd") or layer.get("op", doc["op"]) <= max(layer.get("ip", doc["ip"]), doc["ip"]):
            return False
        if layer.get("ip", doc["ip"]) >= doc["op"] or layer.get("ks", {}).get("o", {}).get("k") == 0:
            return False
        ty = layer.get("ty")
        if ty == 0:
            ref = layer.get("refId")
            return ref not in visited and any(content(l, visited | {ref}) for l in assets.get(ref, {}).get("layers", []))
        if ty == 4:
            return any(v in ("rc", "el", "sh", "sr") for p, v in leaves(layer.get("shapes", [])) if p[-1:] == ("ty",))
        if ty == 5:
            return any(bool(v) for p, v in leaves(layer.get("t", {})) if p[-1:] == ("t",))
        return (ty == 1 and layer.get("sw", 0) > 0 and layer.get("sh", 0) > 0) or (ty == 2 and layer.get("refId") in assets)

    visible = [l for l in doc.get("layers", []) if content(l, set())] if not reasons else []
    if not visible:
        reasons.append("no visible graphic layer")
    def motion(layer, visited):
        if animated(layer):
            return True
        ref = layer.get("refId")
        return ref not in visited and any(content(l, visited | {ref}) and motion(l, visited | {ref})
                                          for l in assets.get(ref, {}).get("layers", []))
    if not any(motion(l, set()) for l in visible):
        reasons.append("no changing keyframed graphic")
    sim = similarity(doc, reference) if reference is not None and not reasons else None
    return {"valid": not reasons, "visible_layers": len(visible), "errors": reasons,
            "similarity": sim, "reward": 0.0 if reasons else (1.0 if sim is None else 0.25 + 0.75*sim)}
