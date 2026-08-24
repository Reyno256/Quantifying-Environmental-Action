"""
train_xgboost.py

XGBoost classifier on 384-dim MiniLM embeddings, trained to distinguish
environmental from non-environmental synagogue web pages.

  - Features  : raw 384-dim embeddings from the DB
  - Labels    : ground truth from America_results.csv (human-coded)
  - Loss      : focal loss (alpha=0.25, gamma=2.0)  — custom XGBoost objective
  - Sampling  : negatives undersampled so positives = 10% of training set
  - Split     : 60% train / 20% val / 20% test  (stratified, seed=42)

Usage:
    python train_xgboost.py
"""

import csv
import os
import sys
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import psycopg2
import xgboost as xgb
from dotenv import load_dotenv

ROOT = Path(__file__).parent
while not (ROOT / ".env").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

SEED          = 42
POS_FRACTION  = 0.10   # positives as fraction of training set after undersampling
FOCAL_ALPHA   = 0.25
FOCAL_GAMMA   = 2.0
THRESHOLD     = 0.50   # default decision threshold (tuned on val)

CATS = [
    "Spirituality & Worship", "Community", "Operations & Maintenance",
    "Energy", "Water", "Waste", "Kitchen", "Environmental & Climate Justice",
]

# ── helpers ───────────────────────────────────────────────────────────────────

def norm_domain(url):
    if not url or not url.strip(): return ""
    url = url.strip().lower().rstrip("/")
    if not url.startswith("http"): url = "http://" + url
    try:
        d = urlparse(url).netloc
        return d[4:] if d.startswith("www.") else d
    except: return ""

def url_to_path(url):
    url = url.strip()
    if not url.startswith("http"): return ""
    p = urlparse(url)
    domain = p.netloc
    domain = domain[4:] if domain.startswith("www.") else domain
    path = p.path.lstrip("/")
    return f"{domain}/{path}" if path else domain

def parse_vec(s):
    return np.fromstring(s.strip("[]"), sep=",", dtype=np.float32)

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

# ── focal loss objective ──────────────────────────────────────────────────────

def focal_loss_obj(preds, dtrain, alpha=FOCAL_ALPHA, gamma=FOCAL_GAMMA):
    """
    Custom XGBoost objective: binary focal loss.
    Returns (grad, hess) w.r.t. raw scores.
    """
    labels = dtrain.get_label()
    p = 1.0 / (1.0 + np.exp(-preds))   # sigmoid

    # Gradient
    # For y=1: dL/ds = alpha*(1-p)^(g-1) * [g*p*(1-p)*log(p) - (1-p)^2]
    # For y=0: dL/ds = (1-a)*p^(g-1) * [p^2 - g*p*(1-p)*log(1-p)]
    g_pos = alpha * (1 - p)**(gamma - 1) * (
        gamma * p * (1 - p) * np.log(p + 1e-8) - (1 - p) ** 2
    )
    g_neg = (1 - alpha) * p ** (gamma - 1) * (
        p ** 2 - gamma * p * (1 - p) * np.log(1 - p + 1e-8)
    )
    grad = np.where(labels == 1, g_pos, g_neg)

    # Hessian (diagonal approximation)
    h_pos = alpha * (1 - p) ** gamma * p * (1 - p)
    h_neg = (1 - alpha) * p ** gamma * p * (1 - p)
    hess  = np.where(labels == 1, h_pos, h_neg)
    hess  = np.maximum(np.abs(hess), 1e-6)

    return grad, hess


def focal_loss_eval(preds, dtrain, alpha=FOCAL_ALPHA, gamma=FOCAL_GAMMA):
    """Evaluation metric: mean focal loss (lower = better)."""
    labels = dtrain.get_label()
    p = 1.0 / (1.0 + np.exp(-preds))
    p_t = np.where(labels == 1, p, 1 - p)
    a_t = np.where(labels == 1, alpha, 1 - alpha)
    loss = -a_t * (1 - p_t) ** gamma * np.log(p_t + 1e-8)
    return "focal_loss", float(loss.mean())


# ── metrics ───────────────────────────────────────────────────────────────────

def prf(y_true, y_pred):
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())
    p  = tp / (tp + fp) if (tp + fp) else 0.0
    r  = tp / (tp + fn) if (tp + fn) else 0.0
    f  = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f, tp, fp, fn, tn


def best_f1_threshold(y_true, probs, n=200):
    """Find threshold on [0,1] that maximises F1."""
    best_t, best_f = 0.5, 0.0
    for t in np.linspace(0.01, 0.99, n):
        pred = (probs >= t).astype(int)
        _, _, f, *_ = prf(y_true, pred)
        if f > best_f:
            best_f, best_t = f, t
    return best_t, best_f


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def roc_auc(y_true, probs):
    """Compute ROC AUC from continuous probability scores."""
    P = int(y_true.sum()); N = len(y_true) - P
    if P == 0 or N == 0:
        return 0.0
    # sort by descending score
    order = np.argsort(-probs)
    y_sorted = y_true[order]
    # accumulate TPR and FPR
    tprs = np.cumsum(y_sorted) / P
    fprs = np.cumsum(1 - y_sorted) / N
    # prepend (0,0) for proper AUC calculation
    tprs = np.concatenate([[0], tprs])
    fprs = np.concatenate([[0], fprs])
    return float(np.trapezoid(tprs, fprs))


# ── PCA (numpy-only, fit on train only) ──────────────────────────────────────

def fit_pca(X_train, var_threshold=0.95):
    """Fit PCA on X_train, return (mean, components, explained_var_ratio)."""
    mean = X_train.mean(axis=0)
    Xc   = X_train - mean
    # economy SVD: shapes U[n,k], S[k], Vt[k,d]
    _, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    explained = (S ** 2) / (S ** 2).sum()
    cumvar = np.cumsum(explained)
    n_components = int(np.searchsorted(cumvar, var_threshold)) + 1
    n_components = min(n_components, X_train.shape[1])
    return mean, Vt[:n_components], explained[:n_components], cumvar[n_components - 1]

def apply_pca(X, mean, components):
    return (X - mean) @ components.T


# ── stratified split (no sklearn) ────────────────────────────────────────────

def stratified_split(X, y, ratios=(0.6, 0.2, 0.2), seed=SEED):
    """60/20/20 stratified split. Returns (X_tr, y_tr, X_val, y_val, X_te, y_te)."""
    rng   = np.random.default_rng(seed)
    pos   = np.where(y == 1)[0]
    neg   = np.where(y == 0)[0]
    rng.shuffle(pos); rng.shuffle(neg)

    def split_idx(idx, r):
        n1 = int(len(idx) * r[0])
        n2 = int(len(idx) * (r[0] + r[1]))
        return idx[:n1], idx[n1:n2], idx[n2:]

    pos_tr, pos_val, pos_te = split_idx(pos, ratios)
    neg_tr, neg_val, neg_te = split_idx(neg, ratios)

    tr_idx  = np.concatenate([pos_tr,  neg_tr])
    val_idx = np.concatenate([pos_val, neg_val])
    te_idx  = np.concatenate([pos_te,  neg_te])

    return (X[tr_idx],  y[tr_idx],
            X[val_idx], y[val_idx],
            X[te_idx],  y[te_idx])


def undersample(X, y, pos_fraction=POS_FRACTION, seed=SEED):
    """Undersample negatives so positives = pos_fraction of returned set."""
    rng  = np.random.default_rng(seed)
    pos  = np.where(y == 1)[0]
    neg  = np.where(y == 0)[0]
    n_neg_keep = int(len(pos) * (1 - pos_fraction) / pos_fraction)
    n_neg_keep = min(n_neg_keep, len(neg))
    neg_sample = rng.choice(neg, n_neg_keep, replace=False)
    idx = np.concatenate([pos, neg_sample])
    rng.shuffle(idx)
    return X[idx], y[idx]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    # ── 1. Load ground truth ─────────────────────────────────────────────────
    print("Loading ground truth…", flush=True)
    with open(ROOT / "09_combined/synagogues_combined.csv", encoding="utf-8") as f:
        pipeline_domains = {norm_domain(r["website"]) for r in csv.DictReader(f) if r.get("website")}
    with open(ROOT / "America_Orig.csv", encoding="latin-1") as f:
        human_domains = {norm_domain(r["Website"]) for r in csv.DictReader(f) if r.get("Website")}
    overlap = (pipeline_domains - {""}) & (human_domains - {""})

    with open(ROOT / "America_results.csv", encoding="latin-1") as f:
        all_results = list(csv.DictReader(f))

    gt_map = {}
    for r in all_results:
        if norm_domain(r.get("Link", "")) not in overlap: continue
        base = url_to_path(r.get("Link", ""))
        if not base: continue
        label = int(any(r.get(c, "").strip() not in ("FALSE", "") for c in CATS))
        for v in [base, base.rstrip("/"), base.rstrip("/")+"/index.html",
                  base.rstrip("/")+".html"]:
            gt_map[v] = label

    # ── 2. Fetch embeddings from DB ───────────────────────────────────────────
    print("Fetching embeddings from DB…", flush=True)
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT wp.id, wp.page_path, wp.embedding::text
        FROM web_pages wp
        WHERE wp.page_path = ANY(%s)
          AND wp.embedding IS NOT NULL
    """, (list(gt_map.keys()),))
    rows = cur.fetchall()
    conn.close()

    ids   = []
    vecs  = []
    labels= []
    for pid, path, vec_str in rows:
        if path not in gt_map: continue
        ids.append(pid)
        vecs.append(parse_vec(vec_str))
        labels.append(gt_map[path])

    X = np.stack(vecs)           # [N, 384]
    y = np.array(labels, dtype=np.float32)
    print(f"  Total pages   : {len(y):,}")
    print(f"  GT positive   : {int(y.sum())}")
    print(f"  GT negative   : {int((y==0).sum())}")

    # ── 3. 60/20/20 stratified split ─────────────────────────────────────────
    X_tr, y_tr, X_val, y_val, X_te, y_te = stratified_split(X, y)
    print(f"\nSplit (before undersampling):")
    print(f"  Train : {len(y_tr):,}  pos={int(y_tr.sum())}")
    print(f"  Val   : {len(y_val):,}  pos={int(y_val.sum())}")
    print(f"  Test  : {len(y_te):,}  pos={int(y_te.sum())}")

    # ── 4. PCA — fit on full training split, transform all three splits ──────
    print("\nFitting PCA on training split (384 dims)…", flush=True)
    pca_mean, pca_components, pca_var, cumvar_at_k = fit_pca(X_tr, var_threshold=0.95)
    n_pca = pca_components.shape[0]
    print(f"  Components kept : {n_pca}  (explains {cumvar_at_k*100:.1f}% variance)")
    X_tr  = apply_pca(X_tr,  pca_mean, pca_components)
    X_val = apply_pca(X_val, pca_mean, pca_components)
    X_te  = apply_pca(X_te,  pca_mean, pca_components)

    # ── 5. Undersample training negatives ────────────────────────────────────
    X_tr, y_tr = undersample(X_tr, y_tr, POS_FRACTION)
    print(f"\nTraining set after undersampling to {POS_FRACTION*100:.0f}% positive:")
    print(f"  Train : {len(y_tr):,}  pos={int(y_tr.sum())}  neg={int((y_tr==0).sum())}  dims={X_tr.shape[1]}")
    actual_frac = y_tr.sum() / len(y_tr)
    print(f"  Actual positive fraction: {actual_frac:.3f}")

    # ── 6. Build DMatrices ────────────────────────────────────────────────────
    dtrain = xgb.DMatrix(X_tr,  label=y_tr)
    dval   = xgb.DMatrix(X_val, label=y_val)
    dtest  = xgb.DMatrix(X_te,  label=y_te)

    # ── 7. Train ──────────────────────────────────────────────────────────────
    params = {
        "max_depth":        6,
        "eta":              0.05,
        "subsample":        0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 1,
        "seed":             SEED,
        "nthread":          -1,
        "disable_default_eval_metric": 1,
    }

    evals_result = {}
    print("\nTraining XGBoost with focal loss…", flush=True)
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=500,
        obj=focal_loss_obj,
        custom_metric=focal_loss_eval,
        evals=[(dtrain, "train"), (dval, "val")],
        evals_result=evals_result,
        early_stopping_rounds=30,
        verbose_eval=50,
    )

    print(f"\nBest iteration : {model.best_iteration}")
    print(f"Best val loss  : {model.best_score:.5f}")

    # ── 7. Find best threshold on validation set ──────────────────────────────
    # Custom objectives return raw margin scores — apply sigmoid for probabilities
    val_probs = sigmoid(model.predict(dval))
    pos_scores = val_probs[y_val == 1]
    neg_scores = val_probs[y_val == 0]
    print(f"\nVal score distributions:")
    print(f"  Positives (n={len(pos_scores)}): mean={pos_scores.mean():.3f}  "
          f"median={np.median(pos_scores):.3f}  max={pos_scores.max():.3f}")
    print(f"  Negatives (n={len(neg_scores)}): mean={neg_scores.mean():.3f}  "
          f"median={np.median(neg_scores):.3f}  max={neg_scores.max():.3f}")

    best_t, best_f1 = best_f1_threshold(y_val, val_probs)
    print(f"\nOptimal threshold (val F1={best_f1:.3f}): {best_t:.3f}")

    # ── 8. Evaluate ───────────────────────────────────────────────────────────
    SEP = "-" * 68

    def report(name, y_true, probs, t):
        pred = (probs >= t).astype(int)
        p, r, f, tp, fp, fn, tn = prf(y_true, pred)
        auc = roc_auc(y_true, probs)
        print(f"\n  {name}")
        print(f"    Threshold : {t:.3f}   ROC AUC : {auc:.3f}")
        print(f"    Flagged   : {int(pred.sum()):,}  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
        print(f"    Precision : {p:.3f}   Recall : {r:.3f}   F1 : {f:.3f}")

    print(f"\n{'='*68}")
    print("EVALUATION RESULTS")
    print(SEP)
    train_probs = sigmoid(model.predict(dtrain))
    report("Train",      y_tr,  train_probs,                    best_t)
    report("Validation", y_val, val_probs,                      best_t)
    report("Test",       y_te,  sigmoid(model.predict(dtest)),  best_t)

    # ── 9. Save model ─────────────────────────────────────────────────────────
    out_path = Path(__file__).parent / "xgboost_focal.ubj"
    model.save_model(str(out_path))
    print(f"\nModel saved -> {out_path}")

    # ── 10. Learning curve ────────────────────────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        tr_loss  = evals_result["train"]["focal_loss"]
        val_loss = evals_result["val"]["focal_loss"]
        rounds   = range(len(tr_loss))

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(rounds, tr_loss,  label="Train focal loss", color="#4C72B0")
        ax.plot(rounds, val_loss, label="Val focal loss",   color="#DD8452")
        ax.axvline(model.best_iteration, color="grey", linestyle="--",
                   lw=1, label=f"Best iter ({model.best_iteration})")
        ax.set_xlabel("Boosting round")
        ax.set_ylabel("Mean focal loss")
        ax.set_title("XGBoost training curve — focal loss", fontweight="bold")
        ax.legend(); ax.spines[["top","right"]].set_visible(False)
        plt.tight_layout()
        fig_path = Path(__file__).parent / "fig_xgb_training.png"
        plt.savefig(fig_path, dpi=150, bbox_inches="tight")
        print(f"Learning curve -> {fig_path}")
    except Exception as e:
        print(f"(Plot skipped: {e})")


if __name__ == "__main__":
    main()
