"""Generate the tolerance-sweep chart for the validation case.

Reads validation_report.json (written by validate_case.py), renders the
sensitivity/coverage tradeoff that justifies the ±0.01 tolerance, and
saves docs/validation_tolerance_sweep.png for the README.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

rep = json.loads(Path("validation_report.json").read_text())
sweep = rep["tolerance_sweep"]
tols = [s["tol"] for s in sweep]
matches = [s["matches"] for s in sweep]
fp = [s["false_matches"] for s in sweep]

fig, ax1 = plt.subplots(figsize=(8, 4.5))

ax1.plot(tols, matches, "o-", color="#2a6f97", linewidth=2,
         label="verified matches")
ax1.set_xlabel("absolute tolerance (±)")
ax1.set_ylabel("verified matches", color="#2a6f97")
ax1.tick_params(axis="y", labelcolor="#2a6f97")
ax1.set_xscale("log")
ax1.set_xticks(tols)
ax1.set_xticklabels([f"±{t}" for t in tols])

ax2 = ax1.twinx()
ax2.plot(tols, fp, "s--", color="#c1121f", linewidth=2,
         label="false matches of known errors")
ax2.set_ylabel("false matches of known errors", color="#c1121f")
ax2.tick_params(axis="y", labelcolor="#c1121f")
ax2.set_ylim(-0.3, max(3, max(fp) + 1))
ax2.yaxis.set_major_locator(MaxNLocator(integer=True))

# annotate the chosen operating point
chosen = next(s for s in sweep if s["tol"] == 0.01)
ax1.axvline(0.01, color="gray", linestyle=":", alpha=0.7)
ax1.annotate(f"chosen: ±0.01\n{chosen['matches']} matches,\n"
             f"{chosen['false_matches']} false",
             xy=(0.01, chosen["matches"]),
             xytext=(0.015, chosen["matches"] - 18),
             arrowprops=dict(arrowstyle="->", color="gray"))

ax1.set_title("Validation case: tolerance sweep (gemma4:31b extraction,\n"
              "233 statistics, both known thesis errors detected at every tolerance)")
fig.tight_layout()
out = Path("docs/validation_tolerance_sweep.png")
fig.savefig(out, dpi=150)
print(f"written: {out}")
