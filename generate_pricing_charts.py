#!/usr/bin/env python3
"""
E-Signature Pricing Comparison Charts
Generates heatmaps showing cost by seats (y-axis) and documents sent (x-axis)
for DocuSign, PandaDoc, and Dropbox Sign across plans and billing terms.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import LinearSegmentedColormap
import os

# ── Axes ──────────────────────────────────────────────────────────────────────
SEATS = [1, 2, 3, 5, 10]
DOCS_PER_MONTH = [5, 10, 25, 50, 100, 200]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "charts")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Cost functions ────────────────────────────────────────────────────────────
# Each returns monthly cost (float) or None if the plan can't serve that combo.

def docusign_personal(seats, docs_mo, annual):
    """1 user only, 5 envelopes/mo."""
    if seats != 1 or docs_mo > 5:
        return None
    return 10.0 if annual else 15.0


def docusign_standard(seats, docs_mo, annual):
    """Multi-user. Annual: 100 env/user/yr ≈ 8.3/user/mo. Monthly: 10 env/user/mo."""
    if seats < 1:
        return None
    cap = (100 / 12) * seats if annual else 10 * seats
    if docs_mo > cap:
        return None
    price = 25.0 if annual else 45.0
    return price * seats


def docusign_business_pro(seats, docs_mo, annual):
    """Same envelope limits as Standard but higher per-seat price."""
    if seats < 1:
        return None
    cap = (100 / 12) * seats if annual else 10 * seats
    if docs_mo > cap:
        return None
    price = 40.0 if annual else 65.0
    return price * seats


def pandadoc_free(seats, docs_mo, annual):
    """Unlimited seats, 5 docs/mo total."""
    if docs_mo > 5:
        return None
    return 0.0


def pandadoc_starter(seats, docs_mo, annual):
    """Unlimited docs."""
    if seats < 1:
        return None
    price = 19.0 if annual else 35.0
    return price * seats


def pandadoc_business(seats, docs_mo, annual):
    """Unlimited docs."""
    if seats < 1:
        return None
    price = 49.0 if annual else 65.0
    return price * seats


# ── Hypothetical PandaDoc (doc-capped, unlimited seats, $4/doc overage) ──────
# Flat fee (unlimited seats) with document caps. Overage at $4/doc beyond cap.
# Monthly plans: check docs_mo against monthly cap.
# Annual plans: check docs_mo * 12 against annual cap.
OVERAGE_PER_DOC = 4.0


def pandadoc_hyp_starter(seats, docs_mo, annual):
    """Hypothetical Starter: flat $19/$35, 15 docs/mo or 180 docs/yr. $4/doc overage. Unlimited seats."""
    if annual:
        annual_docs = docs_mo * 12
        overage = max(0, annual_docs - 180) * OVERAGE_PER_DOC / 12  # spread monthly
        return 19.0 + overage
    else:
        overage = max(0, docs_mo - 15) * OVERAGE_PER_DOC
        return 35.0 + overage


def pandadoc_hyp_business(seats, docs_mo, annual):
    """Hypothetical Business: flat $49/$65, 20 docs/mo or 240 docs/yr. $4/doc overage. Unlimited seats."""
    if annual:
        annual_docs = docs_mo * 12
        overage = max(0, annual_docs - 240) * OVERAGE_PER_DOC / 12  # spread monthly
        return 49.0 + overage
    else:
        overage = max(0, docs_mo - 20) * OVERAGE_PER_DOC
        return 65.0 + overage


def dropbox_free(seats, docs_mo, annual):
    """1 user, 3 requests/mo."""
    if seats != 1 or docs_mo > 3:
        return None
    return 0.0


def dropbox_essentials(seats, docs_mo, annual):
    """1 user only, unlimited sending."""
    if seats != 1:
        return None
    return 15.0  # annual only


def dropbox_standard(seats, docs_mo, annual):
    """Min 2 seats, max ~4 self-serve. Unlimited sending."""
    if seats < 2:
        return None
    return 25.0 * seats  # annual only


def dropbox_premium(seats, docs_mo, annual):
    """Min 5 seats, custom pricing. Estimate ~$35/user/mo."""
    if seats < 5:
        return None
    return 35.0 * seats  # estimated


# ── Plan registry ─────────────────────────────────────────────────────────────
COMPANIES = {
    "DocuSign": [
        ("Personal",     docusign_personal,     True,  True),
        ("Standard",     docusign_standard,      True,  True),
        ("Business Pro", docusign_business_pro,  True,  True),
    ],
    "PandaDoc": [
        ("Free eSign",  pandadoc_free,     True,  False),
        ("Starter",     pandadoc_starter,  True,  True),
        ("Business",    pandadoc_business, True,  True),
    ],
    "Dropbox Sign": [
        ("Free",       dropbox_free,       True,  False),
        ("Essentials", dropbox_essentials, True,  False),
        ("Standard",   dropbox_standard,   True,  False),
        ("Premium\n(est.)", dropbox_premium, True, False),
    ],
}

HYPOTHETICAL = {
    "PandaDoc (Hypothetical)": [
        ("Starter\n∞ seats, flat fee\n15/mo or 180/yr cap\n+$4/doc overage", pandadoc_hyp_starter, True, True),
        ("Business\n∞ seats, flat fee\n20/mo or 240/yr cap\n+$4/doc overage", pandadoc_hyp_business, True, True),
    ],
}
# Tuple: (plan_name, cost_fn, has_annual, has_monthly)


# ── Chart generation ──────────────────────────────────────────────────────────

# Green-to-red cost color map
COST_CMAP = LinearSegmentedColormap.from_list(
    "cost", ["#22c55e", "#fbbf24", "#f97316", "#ef4444", "#991b1b"]
)
GREY = "#e5e7eb"


def make_heatmap(ax, matrix, title, vmin, vmax, annotate=True):
    """Draw a single heatmap on an axes. matrix is (len(SEATS), len(DOCS_PER_MONTH))."""
    display = np.where(np.isnan(matrix), 0, matrix)
    im = ax.imshow(display, cmap=COST_CMAP, vmin=vmin, vmax=vmax, aspect="auto")

    # Overlay grey for N/A cells
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if np.isnan(matrix[i, j]):
                ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                           fill=True, facecolor=GREY, edgecolor="white", lw=1.5))
                ax.text(j, i, "N/A", ha="center", va="center",
                        fontsize=8, color="#6b7280", fontweight="bold")
            elif annotate:
                val = matrix[i, j]
                txt = f"${val:,.0f}"
                # Pick text color for contrast
                color = "white" if val > (vmax * 0.55) else "#1f2937"
                ax.text(j, i, txt, ha="center", va="center",
                        fontsize=8, fontweight="bold", color=color)

    ax.set_xticks(range(len(DOCS_PER_MONTH)))
    ax.set_xticklabels(DOCS_PER_MONTH, fontsize=9)
    ax.set_yticks(range(len(SEATS)))
    ax.set_yticklabels(SEATS, fontsize=9)
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax.set_xlabel("Docs sent / month", fontsize=9)
    ax.set_ylabel("Seats", fontsize=9)
    ax.tick_params(length=0)

    # Grid lines
    for edge in range(len(DOCS_PER_MONTH) + 1):
        ax.axvline(edge - 0.5, color="white", lw=1.5)
    for edge in range(len(SEATS) + 1):
        ax.axhline(edge - 0.5, color="white", lw=1.5)

    return im


def generate_company_chart(company, plans, filename):
    """One figure per company with subplots for each plan × billing term."""
    # Collect all (plan_name, annual_bool, cost_fn) combos
    combos = []
    for name, fn, has_annual, has_monthly in plans:
        if has_annual:
            combos.append((f"{name}\n(Annual)", fn, True))
        if has_monthly:
            combos.append((f"{name}\n(Monthly)", fn, False))

    n = len(combos)
    cols = min(n, 4)
    rows = (n + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(4.8 * cols, 4.2 * rows),
                             squeeze=False)
    fig.suptitle(f"{company} — Monthly Cost by Seats × Docs Sent",
                 fontsize=15, fontweight="bold", y=1.02)

    # Compute matrices and global max for consistent color scale
    matrices = []
    global_max = 0
    for title, fn, annual in combos:
        mat = np.full((len(SEATS), len(DOCS_PER_MONTH)), np.nan)
        for i, s in enumerate(SEATS):
            for j, d in enumerate(DOCS_PER_MONTH):
                cost = fn(s, d, annual)
                if cost is not None:
                    mat[i, j] = cost
                    global_max = max(global_max, cost)
        matrices.append(mat)

    vmin, vmax = 0, max(global_max, 1)

    for idx, ((title, fn, annual), mat) in enumerate(zip(combos, matrices)):
        r, c = divmod(idx, cols)
        im = make_heatmap(axes[r][c], mat, title, vmin, vmax)

    # Hide unused axes
    for idx in range(n, rows * cols):
        r, c = divmod(idx, cols)
        axes[r][c].set_visible(False)

    # Colorbar
    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.6, pad=0.04)
    cbar.set_label("Monthly cost (USD)", fontsize=10)
    cbar.ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    fig.tight_layout()
    path = os.path.join(OUTPUT_DIR, filename)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  Saved {path}")


def generate_side_by_side(filename):
    """
    One big figure: for each billing term, show the cheapest available plan
    per company at each (seats, docs) cell.
    """
    def cheapest(company_plans, seats, docs, annual):
        best = None
        best_name = None
        for name, fn, has_a, has_m in company_plans:
            if annual and not has_a:
                continue
            if not annual and not has_m:
                continue
            cost = fn(seats, docs, annual)
            if cost is not None and (best is None or cost < best):
                best = cost
                best_name = name
        return best

    for annual, term_label, suffix in [(True, "Annual Billing", "annual"),
                                        (False, "Monthly Billing", "monthly")]:
        fig, axes = plt.subplots(1, 3, figsize=(15.5, 5), squeeze=False)
        fig.suptitle(f"Cheapest Plan per Company — {term_label} (Monthly Cost)",
                     fontsize=15, fontweight="bold", y=1.02)

        global_max = 0
        matrices = []
        for company, plans in COMPANIES.items():
            mat = np.full((len(SEATS), len(DOCS_PER_MONTH)), np.nan)
            for i, s in enumerate(SEATS):
                for j, d in enumerate(DOCS_PER_MONTH):
                    c = cheapest(plans, s, d, annual)
                    if c is not None:
                        mat[i, j] = c
                        global_max = max(global_max, c)
            matrices.append(mat)

        vmin, vmax = 0, max(global_max, 1)
        for idx, (company, mat) in enumerate(zip(COMPANIES, matrices)):
            im = make_heatmap(axes[0][idx], mat, company, vmin, vmax)

        cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.75, pad=0.04)
        cbar.set_label("Monthly cost (USD)", fontsize=10)
        cbar.ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

        fig.tight_layout()
        path = os.path.join(OUTPUT_DIR, filename.replace(".png", f"_{suffix}.png"))
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"  Saved {path}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating per-company charts...")
    for company, plans in COMPANIES.items():
        slug = company.lower().replace(" ", "_")
        generate_company_chart(company, plans, f"{slug}_pricing.png")

    print("\nGenerating hypothetical charts...")
    for company, plans in HYPOTHETICAL.items():
        slug = company.lower().replace(" ", "_").replace("(", "").replace(")", "")
        generate_company_chart(company, plans, f"{slug}_pricing.png")

    # Side-by-side: actual PandaDoc vs hypothetical PandaDoc vs each competitor
    print("\nGenerating hypothetical vs actual comparison...")
    hyp_compare = {
        "PandaDoc\n(Actual: Unlimited)": COMPANIES["PandaDoc"],
        "PandaDoc\n(Hypothetical: Capped)": HYPOTHETICAL["PandaDoc (Hypothetical)"],
        "DocuSign": COMPANIES["DocuSign"],
        "Dropbox Sign": COMPANIES["Dropbox Sign"],
    }
    for annual, term_label, suffix in [(True, "Annual Billing", "annual"),
                                        (False, "Monthly Billing", "monthly")]:
        n_companies = len(hyp_compare)
        fig, axes = plt.subplots(1, n_companies, figsize=(4.8 * n_companies, 5),
                                 squeeze=False)
        fig.suptitle(f"Cheapest Plan — {term_label} (PandaDoc Actual vs Hypothetical vs Competitors)",
                     fontsize=13, fontweight="bold", y=1.02)

        def cheapest(company_plans, seats, docs, is_annual):
            best = None
            for name, fn, has_a, has_m in company_plans:
                if is_annual and not has_a:
                    continue
                if not is_annual and not has_m:
                    continue
                cost = fn(seats, docs, is_annual)
                if cost is not None and (best is None or cost < best):
                    best = cost
            return best

        global_max = 0
        matrices = []
        for company, plans in hyp_compare.items():
            mat = np.full((len(SEATS), len(DOCS_PER_MONTH)), np.nan)
            for i, s in enumerate(SEATS):
                for j, d in enumerate(DOCS_PER_MONTH):
                    c = cheapest(plans, s, d, annual)
                    if c is not None:
                        mat[i, j] = c
                        global_max = max(global_max, c)
            matrices.append(mat)

        vmin, vmax = 0, max(global_max, 1)
        for idx, (company, mat) in enumerate(zip(hyp_compare, matrices)):
            im = make_heatmap(axes[0][idx], mat, company, vmin, vmax)

        cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.75, pad=0.04)
        cbar.set_label("Monthly cost (USD)", fontsize=10)
        cbar.ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

        fig.tight_layout()
        path = os.path.join(OUTPUT_DIR, f"hypothetical_comparison_{suffix}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"  Saved {path}")

    print("\nGenerating side-by-side comparison...")
    generate_side_by_side("comparison_cheapest.png")

    print("\nDone! Charts saved to:", OUTPUT_DIR)
