"""
Softmax (multinomial logistic) regression predicting whether a synagogue has
at least one environmental action vs. zero, from its distance to the nearest
OSM feature of each of the 7 environmental site types.

With two outcome classes softmax regression reduces to logistic regression.

Outcome  : has_action  = 1 if n_actions > 0 else 0
Predictors: distance (km) to nearest feature of each type, z-standardized so
            coefficients are comparable and reported per +1 SD of distance.
            green_space, env_hazard, landfill, industrial, abattoir, military,
            airport.

Reported
--------
* Per-feature coefficient, odds ratio (per +1 SD), Wald p-value, 95% CI.
* Likelihood-ratio test of the full model vs. intercept-only
  (H0: none of the 7 distances relate to the outcome) and McFadden pseudo-R^2.
* 5-fold cross-validated accuracy and ROC AUC (sklearn softmax solver) vs the
  majority-class baseline.

Input : action_vs_osm_distance.csv
Outputs:
  action_logit_osm_distance.json
  (summary also printed to stdout)

Usage:
    python 11_analysis/action_logit_osm_distance.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict, StratifiedKFold
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import argparse
import sys

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

OUT = HERE          # reassigned in main() from --source

SITE_TYPES = [
    "green_space", "env_hazard", "landfill",
    "industrial", "abattoir", "military", "airport",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)
    OUT = rc.out_dir(HERE)
    df = pd.read_csv(OUT / "action_vs_osm_distance.csv")

    y = (df["n_actions"] > 0).astype(int).values
    # distances metres -> km, then z-standardize
    Xraw = df[[f"dist_{t}" for t in SITE_TYPES]].values / 1000.0
    scaler = StandardScaler().fit(Xraw)
    Xz = scaler.transform(Xraw)

    n = len(y)
    n_pos = int(y.sum())
    print(f"n = {n}  |  has_action = {n_pos} ({100*n_pos/n:.1f}%)  |  "
          f"zero = {n - n_pos}")

    # ── unregularized logistic fit + manual Wald inference ─────────────────────
    # (statsmodels is unusable against the installed scipy, so we fit with
    #  sklearn penalty=None and derive SEs from the Fisher information.)
    fit = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=2000)
    fit.fit(Xz, y)
    beta = np.concatenate([fit.intercept_, fit.coef_.ravel()])  # [const, 7 dists]

    Xc = np.column_stack([np.ones(n), Xz])             # design matrix w/ intercept
    p_hat = 1.0 / (1.0 + np.exp(-Xc @ beta))
    W = p_hat * (1.0 - p_hat)
    cov = np.linalg.inv((Xc * W[:, None]).T @ Xc)      # (X'WX)^-1
    se = np.sqrt(np.diag(cov))
    z = beta / se
    pvals = 2.0 * stats.norm.sf(np.abs(z))
    odds = np.exp(beta)
    ci_lo = np.exp(beta - 1.96 * se)
    ci_hi = np.exp(beta + 1.96 * se)

    names = ["const"] + SITE_TYPES
    coeffs = []
    print(f"\n{'feature':14} {'coef':>8} {'OR/+1SD':>9} {'p':>11}  95% CI (OR)")
    for i, nm in enumerate(names):
        coeffs.append({
            "feature": nm,
            "coef": round(float(beta[i]), 4),
            "odds_ratio_per_sd": round(float(odds[i]), 4),
            "p_value": float(pvals[i]),
            "or_95ci": [round(float(ci_lo[i]), 4), round(float(ci_hi[i]), 4)],
        })
        if nm != "const":
            print(f"{nm:14} {beta[i]:>8.3f} {odds[i]:>9.3f} {pvals[i]:>11.3e}  "
                  f"[{ci_lo[i]:.3f}, {ci_hi[i]:.3f}]")

    # Log-likelihoods for LR test and pseudo-R^2.
    eps = 1e-12
    ll_full = np.sum(y * np.log(p_hat + eps) + (1 - y) * np.log(1 - p_hat + eps))
    p0 = y.mean()
    ll_null = np.sum(y * np.log(p0 + eps) + (1 - y) * np.log(1 - p0 + eps))
    lr_stat = 2 * (ll_full - ll_null)
    lr_df = len(SITE_TYPES)
    lr_p = stats.chi2.sf(lr_stat, lr_df)
    mcfadden = 1 - ll_full / ll_null

    print(f"\nLikelihood-ratio test (full vs intercept-only): "
          f"chi2({lr_df}) = {lr_stat:.3f}, p = {lr_p:.4f}")
    print(f"McFadden pseudo-R^2 = {mcfadden:.4f}")

    # ── sklearn softmax for predictive performance (cross-validated) ───────────
    clf = make_pipeline(
        StandardScaler(),
        LogisticRegression(solver="lbfgs", max_iter=1000),
    )
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    pred = cross_val_predict(clf, Xraw, y, cv=cv)
    proba = cross_val_predict(clf, Xraw, y, cv=cv, method="predict_proba")[:, 1]
    acc = accuracy_score(y, pred)
    auc = roc_auc_score(y, proba)
    baseline = max(n_pos, n - n_pos) / n

    print(f"\n5-fold CV accuracy = {acc:.4f}  (majority-class baseline "
          f"{baseline:.4f})")
    print(f"5-fold CV ROC AUC  = {auc:.4f}  (chance 0.5)")

    summary = {
        "model": "softmax / logistic regression (2 classes)",
        "outcome": "has_action (>=1 env action) vs zero",
        "predictors": "z-standardized distance (km) to nearest feature, 7 types",
        "n": n, "n_has_action": n_pos,
        "coefficients": coeffs,
        "likelihood_ratio_test": {
            "null_hypothesis": "no distance variable relates to the outcome",
            "chi2": round(float(lr_stat), 4),
            "df": lr_df,
            "p_value": float(lr_p),
        },
        "mcfadden_pseudo_r2": round(float(mcfadden), 4),
        "cv_accuracy": round(float(acc), 4),
        "majority_baseline": round(float(baseline), 4),
        "cv_roc_auc": round(float(auc), 4),
    }
    (OUT / "action_logit_osm_distance.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {OUT/'action_logit_osm_distance.json'}")


if __name__ == "__main__":
    main()
