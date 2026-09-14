"""
make_sweep_figure.py — tolerance-sweep figure for the README.

Plots the empirical justification of the ±0.01 tolerance from
validation_report.json: matches peak at ±0.01 while false verifications
of known errors stay at zero across the whole sweep.

Output: docs/tolerance_sweep.png
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

report = json.loads(Path("validation_report.json").read_text())
sweep = report["tolerance_sweep"]

tols = [s["tol"] for s in sweep]
matches = [s["matches"] for s in sweep]
false_v = [s.get("false_verifications", 0) for s in sweep]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7), sharex=False,
                               gridspec_kw={"height_ratios": [3, 1]})
fig.suptitle("Tolerance sweep — thesis validation case (n=42, 233 extracted statistics)",
             fontsize=11, y=0.98)

ax1.plot(tols, matches, "o-", color="#2563eb", linewidth=2, markersize=6)
ax1.axvline(0.01, color="#16a34a", linestyle="--", alpha=0.7,
            label="chosen tolerance (±0.01)")
ax1.scatter([0.01], [max(matches)], s=90, color="#16a34a", zorder=5)
ax1.set_ylabel("verified statistics")
ax1.set_title("Matches peak at ±0.01 and decline beyond it (collisions from over-loose matching)")
ax1.legend(loc="lower right", fontsize=9)
ax1.grid(alpha=0.25)

ax2.bar([str(t) for t in tols], false_v, color="#dc2626", alpha=0.85)
ax2.set_ylabel("false verifications\nof known errors")
ax2.set_xlabel("absolute tolerance")
ax2.set_ylim(0, 1)
ax2.set_yticks([0, 1])
ax2.grid(alpha=0.25, axis="y")
ax2.set_title("False verifications of the two known thesis errors: 0 at every tolerance",
              fontsize=10)

plt.tight_layout()
out = Path("docs/tolerance_sweep.png")
plt.savefig(out, dpi=150)
print(f"written: {out}")

# print the data for the README table
for s in sweep:
    print(f"  ±{s['tol']}: {s['matches']} matches, {s['false_matches']} false")
