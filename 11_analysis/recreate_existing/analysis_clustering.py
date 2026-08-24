"""
Clustering (goals.md line 7: "split by total action count (clustering?)").

Tests whether environmentally active synagogues form meaningful engagement
clusters, rather than assuming Low/Med/High bins. k-means is fit for k=2..8 and
judged by the silhouette score (+ Davies-Bouldin, elbow inertia). If a defensible
k exists the clusters are profiled; if silhouettes are low we report that there is
no strong cluster structure (a valid finding).

Feature vector per env-active synagogue (standardized):
  * 9 per-category action-chunk counts
  * total action-chunk count
  * denomination (ordinal: thesis denomination rank, Unknown -> separate level)
  * district political lean (Dem=+1, Rep=-1, unknown=0)

Outputs:
  clustering.json
  fig_cluster_silhouette.png   silhouette / Davies-Bouldin / inertia vs k
  fig_cluster_scatter.png      2-D PCA scatter colored by best-k cluster
  fig_cluster_profiles.png     category composition per cluster (if best k chosen)

Outputs go to page_chunks/ when --source page_chunks is given.

    python analysis_clustering.py [--source page_chunks]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, davies_bouldin_score
from sklearn.preprocessing import StandardScaler

import recreate_common as rc

HERE = Path(__file__).resolve().parent
K_RANGE = range(2, 9)
SILHOUETTE_OK = 0.25   # below this we treat structure as weak/absent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)          # must precede any outlier-set use
    out_path = rc.out_dir(HERE)

    conn = rc.get_conn()
    actions = rc.load_actions(conn)
    denom = rc.active_denominator(conn)
    syn = pd.read_sql(
        "SELECT id AS synagogue_id, denomination_canonical AS denomination, "
        "congressional_district FROM synagogues", conn)
    syn["denomination"] = rc.merge_denominations(syn["denomination"])  # Modern Orthodox -> Orthodox
    lean = rc.district_lean(2024, conn)
    conn.close()

    env_ids = sorted(set(actions["synagogue_id"].unique()) & denom)
    m = rc.category_count_matrix(actions, env_ids)        # rows = env-active syn
    feat = m.copy()
    feat["total_actions"] = feat.sum(axis=1)

    sy = syn.set_index("synagogue_id").reindex(env_ids)
    denom_rank = {d: i for i, d in enumerate(rc.DENOM_ORDER)}
    feat["denom_ordinal"] = sy["denomination"].map(denom_rank).fillna(len(rc.DENOM_ORDER)).values
    lean_num = sy["congressional_district"].map(lean).map({"Dem": 1, "Rep": -1}).fillna(0)
    feat["district_lean"] = lean_num.values

    feature_cols = list(rc.CATEGORIES) + ["total_actions", "denom_ordinal", "district_lean"]
    X = StandardScaler().fit_transform(feat[feature_cols].values)

    # sweep k
    sweep = []
    labels_by_k = {}
    for k in K_RANGE:
        km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(X)
        labels_by_k[k] = km.labels_
        sweep.append({"k": k,
                      "silhouette": round(float(silhouette_score(X, km.labels_)), 4),
                      "davies_bouldin": round(float(davies_bouldin_score(X, km.labels_)), 4),
                      "inertia": round(float(km.inertia_), 1)})
    best = max(sweep, key=lambda s: s["silhouette"])
    best_k = best["k"]
    structure = "meaningful" if best["silhouette"] >= SILHOUETTE_OK else "weak/none"

    # profile best-k clusters
    labels = labels_by_k[best_k]
    feat["cluster"] = labels
    profiles = []
    for cl in range(best_k):
        sub = feat[feat["cluster"] == cl]
        sids = sub.index
        dom_denom = sy.loc[sids, "denomination"].value_counts().idxmax() if len(sids) else None
        leandist = lean_num.loc[sids]
        profiles.append({
            "cluster": cl, "n": int(len(sub)),
            "mean_total_actions": round(float(sub["total_actions"].mean()), 2),
            "category_means": {c: round(float(sub[c].mean()), 2) for c in rc.CATEGORIES},
            "dominant_denomination": dom_denom,
            "pct_dem_district": round(100 * float((leandist > 0).mean()), 1),
            "pct_rep_district": round(100 * float((leandist < 0).mean()), 1),
        })
    profiles.sort(key=lambda p: p["mean_total_actions"], reverse=True)

    out = {
        "source": rc.get_source(),
        "n_env_active": len(env_ids),
        "features": feature_cols,
        "k_sweep": sweep,
        "best_k": best_k,
        "best_silhouette": best["silhouette"],
        "silhouette_threshold": SILHOUETTE_OK,
        "cluster_structure": structure,
        "interpretation": (
            f"Best silhouette {best['silhouette']:.3f} at k={best_k} "
            f"({'above' if best['silhouette'] >= SILHOUETTE_OK else 'below'} the "
            f"{SILHOUETTE_OK} threshold) → {structure} cluster structure. "
            + ("Synagogues separate into distinct engagement clusters."
               if structure == "meaningful" else
               "Engagement varies continuously rather than in discrete clusters; "
               "quantile bins on total action count are a more honest summary.")),
        "cluster_profiles": profiles,
    }
    (out_path / "clustering.json").write_text(json.dumps(out, indent=2))
    _plot(sweep, best_k, X, labels, profiles, out_path)
    print(json.dumps({k: out[k] for k in
                      ("best_k", "best_silhouette", "cluster_structure", "interpretation")}, indent=2))
    print("\nk sweep:")
    for s in sweep:
        print(f"  k={s['k']}  silhouette={s['silhouette']:.3f}  DB={s['davies_bouldin']:.3f}")


def _plot(sweep, best_k, X, labels, profiles, out_path):
    plt = rc.apply_style()
    ks = [s["k"] for s in sweep]

    # silhouette / DB / inertia vs k
    fig, ax = plt.subplots(1, 3, figsize=(16, 5.5))
    ax[0].plot(ks, [s["silhouette"] for s in sweep], "o-", color="#4c72b0")
    ax[0].axhline(SILHOUETTE_OK, ls="--", color="gray", lw=1)
    ax[0].axvline(best_k, ls=":", color="red", lw=1)
    ax[0].set_title("Silhouette", pad=4); ax[0].set_xlabel("k")
    ax[1].plot(ks, [s["davies_bouldin"] for s in sweep], "o-", color="#c44e52")
    ax[1].set_title("Davies-Bouldin", pad=4); ax[1].set_xlabel("k")
    ax[2].plot(ks, [s["inertia"] for s in sweep], "o-", color="#55a868")
    ax[2].set_title("Inertia", pad=4); ax[2].set_xlabel("k")
    fig.tight_layout()
    fig.savefig(out_path / "fig_cluster_silhouette.png", bbox_inches="tight")

    # 2-D PCA scatter colored by cluster
    pcs = PCA(n_components=2, random_state=42).fit_transform(X)
    fig2, ax2 = plt.subplots(figsize=(7, 6))
    sc = ax2.scatter(pcs[:, 0], pcs[:, 1], c=labels, cmap="tab10", s=14, alpha=0.7)
    ax2.set_xlabel("PC1"); ax2.set_ylabel("PC2")
    ax2.grid(False)
    plt.colorbar(sc, ax=ax2, label="cluster")
    fig2.tight_layout()
    fig2.savefig(out_path / "fig_cluster_scatter.png")

    # cluster category-composition profiles
    fig3, ax3 = plt.subplots(figsize=(13, 7))
    x = np.arange(len(profiles))
    bottom = np.zeros(len(profiles))
    for cat in rc.CATEGORIES:
        vals = np.array([p["category_means"][cat] for p in profiles])
        ax3.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
                edgecolor="white", linewidth=0.4)
        bottom += vals
    ax3.set_xticks(x)
    ax3.set_xticklabels([f"cluster {p['cluster']}\nn={p['n']}\n{p['dominant_denomination']}"
                         for p in profiles])
    ax3.set_ylabel("Mean action count per synagogue")
    ax3.legend(title="Category", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig3.tight_layout()
    fig3.savefig(out_path / "fig_cluster_profiles.png")


if __name__ == "__main__":
    main()
