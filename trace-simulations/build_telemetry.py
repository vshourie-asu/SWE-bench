#!/usr/bin/env python3
"""Turn mini-SWE-agent trajectories into graph-friendly telemetry.

mini-SWE-agent writes one ``<instance_id>/<instance_id>.traj.json`` per instance plus a
``preds.json`` into its output directory. Those files are complete but awkward to graph.
This script normalizes them into a stable, well-documented schema that downstream
researchers can load directly into graph tools:

For each instance it writes, under ``telemetry/<instance_id>/``:

  events.jsonl   One JSON object per agent step/message, in order. The flat event log.
  graph.json     {"nodes": [...], "edges": [...]} -- a directed graph of the trajectory.
  graph.graphml  The same graph in GraphML, loadable by Gephi, Cytoscape, networkx, etc.

And at the telemetry root:

  manifest.json  One row per instance: ids, model, exit_status, cost, step/node counts,
                 resolved verdict (if an evaluation results.json is provided), and the
                 relative paths to that instance's artifacts.

Graph model
-----------
Nodes are agent steps. Each message in the trajectory becomes a node with:
    id, step_index, role (system|user|assistant|exit), kind, text (truncated preview),
    full_text_len, actions (shell commands for assistant steps), cost, is_submission.
Edges are:
    "next"        step N -> step N+1 (temporal flow of the whole trajectory)
    "acts_on"     an assistant step -> the observation (user) step that follows it
This is enough to render the agent's loop as a graph and to compute things like branch
depth, action counts, and cost-per-step.

Usage
-----
    python build_telemetry.py --preds logs/preds/<run-id> --out telemetry
    python build_telemetry.py --preds logs/preds/<run-id> --out telemetry \
        --results logs/evaluation/<run-id>/results.json
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PREVIEW_CHARS = 2000  # how much message text to inline in nodes; full text stays in events.jsonl


def find_trajectories(preds_dir: Path) -> list[Path]:
    """Every *.traj.json under the predictions directory."""
    return sorted(preds_dir.glob("*/*.traj.json"))


def load_results(results_path: Path | None) -> dict[str, bool]:
    """Map instance_id -> resolved(bool) from an evaluation results.json, if given.

    The harness summary lists resolved/unresolved instance ids; we accept a few common
    shapes so this keeps working if the field names shift slightly.
    """
    if not results_path or not results_path.exists():
        return {}
    data = json.loads(results_path.read_text())
    resolved = set()
    unresolved = set()
    for key in ("resolved", "resolved_instances", "resolved_ids"):
        resolved |= set(data.get(key, []) or [])
    for key in ("unresolved", "unresolved_instances", "unresolved_ids"):
        unresolved |= set(data.get(key, []) or [])
    verdict = {iid: True for iid in resolved}
    verdict.update({iid: False for iid in unresolved})
    return verdict


def _preview(text: Any) -> str:
    s = text if isinstance(text, str) else json.dumps(text)
    return s[:PREVIEW_CHARS]


def message_to_event(idx: int, msg: dict) -> dict:
    """Normalize one trajectory message into a flat telemetry event."""
    role = msg.get("role", "")
    extra = msg.get("extra", {}) or {}
    actions = extra.get("actions", []) or []
    content = msg.get("content", "")
    kind = {
        "assistant": "action" if actions else "thought",
        "user": "observation",
        "system": "system",
        "exit": "exit",
    }.get(role, role or "unknown")
    return {
        "step_index": idx,
        "role": role,
        "kind": kind,
        "actions": actions,
        "cost": extra.get("cost", 0.0),
        "exit_status": extra.get("exit_status"),
        "is_submission": bool(extra.get("submission")),
        "text": content,
        "full_text_len": len(content) if isinstance(content, str) else None,
    }


def build_graph(instance_id: str, events: list[dict]) -> dict:
    """Build a node/edge graph from the ordered events of one instance."""
    nodes = []
    for ev in events:
        nodes.append(
            {
                "id": f"{instance_id}:{ev['step_index']}",
                "step_index": ev["step_index"],
                "role": ev["role"],
                "kind": ev["kind"],
                "text": _preview(ev["text"]),
                "full_text_len": ev["full_text_len"],
                "actions": ev["actions"],
                "n_actions": len(ev["actions"]),
                "cost": ev["cost"],
                "is_submission": ev["is_submission"],
            }
        )

    edges = []
    # temporal flow
    for a, b in zip(events, events[1:]):
        edges.append(
            {
                "source": f"{instance_id}:{a['step_index']}",
                "target": f"{instance_id}:{b['step_index']}",
                "type": "next",
            }
        )
    # action -> its observation: an assistant step with actions, followed by an observation
    for a, b in zip(events, events[1:]):
        if a["kind"] == "action" and b["kind"] == "observation":
            edges.append(
                {
                    "source": f"{instance_id}:{a['step_index']}",
                    "target": f"{instance_id}:{b['step_index']}",
                    "type": "acts_on",
                }
            )
    return {"instance_id": instance_id, "nodes": nodes, "edges": edges}


def graph_to_graphml(graph: dict) -> str:
    """Serialize a graph dict to GraphML (Gephi / Cytoscape / networkx readable)."""
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")

    node_keys = {
        "role": "string",
        "kind": "string",
        "step_index": "long",
        "n_actions": "long",
        "cost": "double",
        "is_submission": "boolean",
        "full_text_len": "long",
        "label": "string",
    }
    for name, typ in node_keys.items():
        k = ET.SubElement(root, f"{{{ns}}}key")
        k.set("id", f"n_{name}")
        k.set("for", "node")
        k.set("attr.name", name)
        k.set("attr.type", typ)
    etype = ET.SubElement(root, f"{{{ns}}}key")
    etype.set("id", "e_type")
    etype.set("for", "edge")
    etype.set("attr.name", "type")
    etype.set("attr.type", "string")

    g = ET.SubElement(root, f"{{{ns}}}graph")
    g.set("edgedefault", "directed")
    g.set("id", graph["instance_id"])

    for node in graph["nodes"]:
        n = ET.SubElement(g, f"{{{ns}}}node")
        n.set("id", node["id"])
        label = f"{node['step_index']}:{node['kind']}"
        values = {
            "role": node["role"],
            "kind": node["kind"],
            "step_index": node["step_index"],
            "n_actions": node["n_actions"],
            "cost": node["cost"],
            "is_submission": str(node["is_submission"]).lower(),
            "full_text_len": node["full_text_len"] if node["full_text_len"] is not None else 0,
            "label": label,
        }
        for name, val in values.items():
            d = ET.SubElement(n, f"{{{ns}}}data")
            d.set("key", f"n_{name}")
            d.text = str(val)

    for i, edge in enumerate(graph["edges"]):
        e = ET.SubElement(g, f"{{{ns}}}edge")
        e.set("id", f"e{i}")
        e.set("source", edge["source"])
        e.set("target", edge["target"])
        d = ET.SubElement(e, f"{{{ns}}}data")
        d.set("key", "e_type")
        d.text = edge["type"]

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def process_trajectory(traj_path: Path, out_root: Path, verdict: dict[str, bool]) -> dict:
    # utf-8-sig tolerates a BOM if one slipped in; plain utf-8 files parse fine too.
    data = json.loads(traj_path.read_text(encoding="utf-8-sig"))
    instance_id = data.get("instance_id") or traj_path.stem.replace(".traj", "")
    info = data.get("info", {}) or {}
    messages = data.get("messages", []) or []

    events = [message_to_event(i, m) for i, m in enumerate(messages)]
    graph = build_graph(instance_id, events)

    inst_dir = out_root / instance_id
    inst_dir.mkdir(parents=True, exist_ok=True)

    with (inst_dir / "events.jsonl").open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    (inst_dir / "graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    (inst_dir / "graph.graphml").write_text(graph_to_graphml(graph), encoding="utf-8")

    model_stats = info.get("model_stats", {}) or {}
    n_actions = sum(len(ev["actions"]) for ev in events)
    return {
        "instance_id": instance_id,
        "model_name_or_path": (data.get("model", {}) or {}).get("model_name")
        or info.get("config", {}).get("agent", {}).get("model_name"),
        "exit_status": info.get("exit_status"),
        "has_submission": bool(info.get("submission")),
        "resolved": verdict.get(instance_id),
        "n_steps": len(events),
        "n_actions": n_actions,
        "api_calls": model_stats.get("api_calls"),
        "instance_cost": model_stats.get("instance_cost"),
        "mini_version": info.get("mini_version"),
        "trajectory_format": data.get("trajectory_format"),
        "source_trajectory": str(traj_path),
        "artifacts": {
            "events": f"{instance_id}/events.jsonl",
            "graph_json": f"{instance_id}/graph.json",
            "graphml": f"{instance_id}/graph.graphml",
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preds", required=True, type=Path, help="mini-SWE-agent output dir (has preds.json + */*.traj.json)")
    ap.add_argument("--out", required=True, type=Path, help="Where to write telemetry artifacts")
    ap.add_argument("--results", type=Path, default=None, help="Optional evaluation results.json for resolved verdicts")
    args = ap.parse_args()

    trajs = find_trajectories(args.preds)
    if not trajs:
        raise SystemExit(f"No *.traj.json found under {args.preds}. Did inference run and finish?")

    verdict = load_results(args.results)
    args.out.mkdir(parents=True, exist_ok=True)

    manifest = []
    for traj in trajs:
        row = process_trajectory(traj, args.out, verdict)
        manifest.append(row)
        print(f"  {row['instance_id']}: {row['n_steps']} steps, {row['n_actions']} actions, "
              f"exit={row['exit_status']}, resolved={row['resolved']}")

    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote telemetry for {len(manifest)} instance(s) to {args.out}")
    print(f"Manifest: {args.out / 'manifest.json'}")


if __name__ == "__main__":
    main()
