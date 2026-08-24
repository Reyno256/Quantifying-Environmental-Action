"""
Overview (goals.md lines 1-3): how many synagogues took >= 1 environmental
action, and the mean action frequency per synagogue (all vs environmentally
active). Reproduces thesis section 4.3.1 / Table 4 (Total row).

Primary metric = action chunks (frequency). The distinct-action-type mean is
also reported as a fidelity check against the thesis (expected ~4.2).

Outputs go to page_chunks/ when --source page_chunks is given.

    python analysis_overview.py [--source page_chunks]
"""

import argparse
import json
from pathlib import Path

import recreate_common as rc

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)          # must precede any outlier-set use
    out_path = rc.out_dir(HERE)

    conn = rc.get_conn()
    actions = rc.load_actions(conn)
    denom = rc.active_denominator(conn)
    conn.close()

    active_ids = denom                                   # all "active" synagogues (denominator)
    env_ids = set(actions["synagogue_id"].unique()) & denom   # environmentally active

    n_total = len(active_ids)
    n_env = len(env_ids)

    # chunk-count (frequency) metric
    m_all = rc.category_count_matrix(actions, active_ids)
    m_env = rc.category_count_matrix(actions, env_ids)
    chunks_all = m_all.sum(axis=1)
    chunks_env = m_env.sum(axis=1)

    # distinct-action-type metric (thesis-comparable)
    dt = actions[actions["synagogue_id"].isin(env_ids)].groupby("synagogue_id")["action"].nunique()
    dt_all = dt.reindex(list(active_ids), fill_value=0)

    out = {
        "source": rc.get_source(),
        "denominator_definition": "synagogues with >=1 successfully crawled web page (has_error=FALSE)",
        "n_active_denominator": int(n_total),
        "n_environmentally_active": int(n_env),
        "pct_environmentally_active": round(100 * n_env / n_total, 1),
        "pct_env_active_ci95": round(100 * rc.ci95_prop(n_env, n_total), 1),
        "action_metric": "action chunks (non-N/A classifications)",
        "mean_action_chunks_all": round(float(chunks_all.mean()), 2),
        "mean_action_chunks_per_active": round(float(chunks_env.mean()), 2),
        "ci95_action_chunks_per_active": round(rc.ci95_mean(chunks_env), 2),
        "fidelity_check": {
            "metric": "distinct action types (thesis definition)",
            "mean_distinct_types_all": round(float(dt_all.mean()), 2),
            "mean_distinct_types_per_active": round(float(dt.mean()), 2),
            "thesis_all": 1.8,
            "thesis_per_active": 4.2,
        },
        "total_action_chunks": int(chunks_env.sum()),
    }

    (out_path / "overview.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
