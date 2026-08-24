"""
kappa_power_simulation.py

Monte Carlo power / sample-size analysis for Cohen's kappa on the binary
(any environmental action vs. N/A) agreement question between a human rater
and Gemini, as recorded by label_gemini_chunks.py.

Why simulation instead of the standard closed-form kappa test (e.g. Donner &
Eliasziw 1992, as implemented in kappaSize): that method assumes both raters
share a single marginal prevalence, and its chi-square reference distribution
is only asymptotically valid when expected cell counts aren't too small.
Neither holds well here -- Gemini and the human rater have substantially
different positive rates (real bias, not just chance disagreement: ~1% vs
~4% at the original n=100 check), and with positives this rare, modest
samples routinely produce a near-empty cell. This script instead simulates
the *exact* finite-sample distribution of kappa-hat directly from the two
observed marginals, at whatever n is actually being asked about, so it
carries no large-sample assumption at any point.

Method: given a target kappa and the two (possibly unequal) marginal
positive rates, invert Cohen's kappa definition to get the implied 2x2 joint
distribution (joint_from_kappa), then draw M independent n-sized multinomial
samples from that distribution and compute kappa-hat for each. Doing this
once under H0: kappa=kappa0 gives the empirical null distribution (and thus
a one-sided critical value at level alpha); doing it again under the
assumed true kappa1 gives power as the fraction exceeding that critical
value.

Usage:
    # Power at the current sample size, using the data collected so far
    python kappa_power_simulation.py

    # Power at a specific n, without needing existing data
    python kappa_power_simulation.py --n 100 --p-human 0.01 --p-gemini 0.04 --kappa1 0.39

    # Find the n needed for 80% power, scanning a grid of sample sizes
    python kappa_power_simulation.py --find-n --target-power 0.8
"""

import argparse
import csv
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
DEFAULT_IN = HERE / "gemini_chunk_irr.csv"


# ── observed data -> marginals / kappa ─────────────────────────────────────

def gemini_bucket(row: dict) -> str:
    return row["gemini_major_category"] or "N/A"


def load_observed(csv_path: Path):
    """Read a label_gemini_chunks.py output CSV and return the binary
    (action vs. N/A) confusion counts, marginals, and observed kappa."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["human_label"] != "Skip"]

    n = len(rows)
    TP = sum(1 for r in rows if r["human_label"] != "N/A" and gemini_bucket(r) != "N/A")
    FN = sum(1 for r in rows if r["human_label"] != "N/A" and gemini_bucket(r) == "N/A")
    FP = sum(1 for r in rows if r["human_label"] == "N/A" and gemini_bucket(r) != "N/A")
    TN = sum(1 for r in rows if r["human_label"] == "N/A" and gemini_bucket(r) == "N/A")
    return kappa_from_counts(TP, FP, FN, TN, n)


def kappa_from_counts(TP: int, FP: int, FN: int, TN: int, n: int):
    p_h = (TP + FN) / n   # human positive rate
    p_g = (TP + FP) / n   # gemini positive rate
    po = (TP + TN) / n
    pe = p_h * p_g + (1 - p_h) * (1 - p_g)
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return {"n": n, "TP": TP, "FP": FP, "FN": FN, "TN": TN,
            "p_h": p_h, "p_g": p_g, "pe": pe, "kappa": kappa}


# ── kappa -> joint distribution (standard inversion) ───────────────────────

def joint_from_kappa(kappa: float, p_h: float, p_g: float, pe: float) -> np.ndarray:
    """Invert Cohen's kappa definition for fixed marginals p_h, p_g to
    recover the 2x2 joint distribution [p11, p12, p21, p22] =
    [(h+,g+), (h+,g-), (h-,g+), (h-,g-)] consistent with that kappa.

    Not every (kappa, p_h, p_g) combination is feasible -- given fixed,
    unequal marginals there's only a bounded range of kappa a real joint
    distribution can produce (the kappa "prevalence/bias paradox"). Returns
    a vector that may contain negative entries; callers should check."""
    po = kappa * (1 - pe) + pe
    p11 = (po - 1 + p_h + p_g) / 2
    p12 = p_h - p11
    p21 = p_g - p11
    p22 = 1 - p_h - p_g + p11
    return np.array([p11, p12, p21, p22])


def is_feasible(probs: np.ndarray) -> bool:
    return bool((probs >= 0).all())


# ── simulation ──────────────────────────────────────────────────────────────

def simulate_kappas(probs: np.ndarray, n: int, M: int, rng: np.random.Generator) -> np.ndarray:
    draws = rng.multinomial(n, probs, size=M)
    p = draws / n
    po = p[:, 0] + p[:, 3]
    p_h = p[:, 0] + p[:, 1]
    p_g = p[:, 0] + p[:, 2]
    pe = p_h * p_g + (1 - p_h) * (1 - p_g)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(pe < 1, (po - pe) / (1 - pe), np.nan)


def power_at_n(n: int, p_h: float, p_g: float, kappa0: float, kappa1: float,
               alpha: float, M: int, rng: np.random.Generator):
    """One-sided test H0: kappa <= kappa0 vs H1: kappa > kappa0. Returns
    (power, critical_value), or (None, None) if the (kappa, marginals)
    combination isn't a feasible joint distribution at either hypothesis."""
    pe = p_h * p_g + (1 - p_h) * (1 - p_g)
    probs_null = joint_from_kappa(kappa0, p_h, p_g, pe)
    probs_alt = joint_from_kappa(kappa1, p_h, p_g, pe)
    if not (is_feasible(probs_null) and is_feasible(probs_alt)):
        return None, None

    null_kappas = simulate_kappas(probs_null, n, M, rng)
    alt_kappas = simulate_kappas(probs_alt, n, M, rng)
    crit = np.nanquantile(null_kappas, 1 - alpha)
    power = np.nanmean(alt_kappas > crit)
    return power, crit


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN),
                     help="label_gemini_chunks.py output CSV to read observed "
                          "marginals/kappa from (default: gemini_chunk_irr.csv)")
    ap.add_argument("--n", type=int, default=None,
                     help="sample size to evaluate power at (default: the "
                          "actual row count in --in)")
    ap.add_argument("--p-human", type=float, default=None,
                     help="human positive rate, overrides value loaded from --in")
    ap.add_argument("--p-gemini", type=float, default=None,
                     help="gemini positive rate, overrides value loaded from --in")
    ap.add_argument("--kappa0", type=float, default=0.2,
                     help="null-hypothesis kappa (default 0.2)")
    ap.add_argument("--kappa1", type=float, default=None,
                     help="assumed true kappa, overrides the observed kappa "
                          "loaded from --in")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--reps", type=int, default=200_000,
                     help="Monte Carlo repetitions per hypothesis (default 200,000)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--find-n", action="store_true",
                     help="instead of a single power check, scan a grid of n "
                          "to find the smallest n reaching --target-power")
    ap.add_argument("--target-power", type=float, default=0.8)
    ap.add_argument("--n-grid", type=int, nargs="+",
                     default=[100, 200, 300, 500, 700, 900, 1100, 1300, 1500, 2000])
    args = ap.parse_args()

    in_path = Path(args.in_path)
    observed = load_observed(in_path) if in_path.exists() else None
    if observed:
        print(f"Loaded {observed['n']} rated chunks from {in_path}")
        print(f"  Confusion (human vs. gemini): TP={observed['TP']} FP={observed['FP']} "
              f"FN={observed['FN']} TN={observed['TN']}")
        print(f"  Observed marginals: p_human+={observed['p_h']:.4f}  "
              f"p_gemini+={observed['p_g']:.4f}  pe={observed['pe']:.4f}")
        print(f"  Observed kappa: {observed['kappa']:.4f}\n")
    elif args.p_human is None or args.p_gemini is None:
        print(f"No such file: {in_path}, and --p-human/--p-gemini not both given.")
        return

    p_h = args.p_human if args.p_human is not None else observed["p_h"]
    p_g = args.p_gemini if args.p_gemini is not None else observed["p_g"]
    kappa1 = args.kappa1 if args.kappa1 is not None else observed["kappa"]
    n_default = observed["n"] if observed else None

    rng = np.random.default_rng(args.seed)

    print(f"Marginals: p_human+={p_h:.4f}  p_gemini+={p_g:.4f}  "
          f"kappa0={args.kappa0}  assumed true kappa={kappa1:.4f}\n")

    if args.find_n:
        print(f"{'n':>6} {'power':>8} {'crit':>8}")
        crossed = None
        for n in args.n_grid:
            power, crit = power_at_n(n, p_h, p_g, args.kappa0, kappa1, args.alpha, args.reps, rng)
            if power is None:
                print(f"{n:6d}   infeasible kappa/marginal combination at this n")
                continue
            print(f"{n:6d} {power:8.3f} {crit:8.3f}")
            if crossed is None and power >= args.target_power:
                crossed = n
        if crossed:
            print(f"\nSmallest grid point reaching {args.target_power:.0%} power: n={crossed}")
        else:
            print(f"\nNo grid point reached {args.target_power:.0%} power -- extend --n-grid.")
        return

    n = args.n if args.n is not None else n_default
    if n is None:
        print("No --n given and no --in file to infer a default sample size from.")
        return
    power, crit = power_at_n(n, p_h, p_g, args.kappa0, kappa1, args.alpha, args.reps, rng)
    if power is None:
        print(f"n={n}: infeasible kappa/marginal combination -- "
              f"kappa1={kappa1:.3f} isn't reachable with these marginals.")
        return
    print(f"Simulation-based (n={n}, alpha={args.alpha}, M={args.reps:,} reps):")
    print(f"  One-sided critical value (reject H0: kappa<={args.kappa0} if kappa_hat > c): c = {crit:.3f}")
    print(f"  Power (one-sided, H1: true kappa = {kappa1:.3f}): {power:.3f}")


if __name__ == "__main__":
    main()
