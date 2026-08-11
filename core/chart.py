import io
import matplotlib
matplotlib.use("Agg")  # non-interactive backend, safe for a server/bot process
import matplotlib.pyplot as plt

# DejaVu Sans (matplotlib's default) supports Cyrillic glyphs out of the
# box, so Ukrainian category labels render correctly with no extra setup.


def generate_category_chart(expense_by_category: list, title: str, top_n: int = 10, lang: str = "uk"):
    """
    Render a horizontal bar chart of expenses by category.

    Args:
        expense_by_category: [(category, amount), ...] sorted descending
            by amount (as returned in a report summary).
        title: chart title, e.g. "Витрати за Липень 2026".
        top_n: cap the number of bars shown, to keep the chart readable
            when there are many categories.
        lang: "uk" or "en" — controls the x-axis label language.

    Returns:
        A BytesIO buffer containing a PNG image, or None if there's no
        expense data to chart.
    """
    if not expense_by_category:
        return None

    data = expense_by_category[:top_n]
    # Reverse so the largest category ends up at the top of the chart
    # (barh plots bottom-to-top).
    categories = [c for c, _ in data][::-1]
    amounts = [a for _, a in data][::-1]

    fig_height = max(3, 0.5 * len(categories) + 1)
    fig, ax = plt.subplots(figsize=(8, fig_height))
    bars = ax.barh(categories, amounts, color="#4C9AFF")

    ax.set_xlabel("Amount, грн" if lang == "en" else "Сума, грн")
    ax.set_title(title)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    max_amount = max(amounts) if amounts else 0
    for bar, amount in zip(bars, amounts):
        ax.text(
            bar.get_width() + max_amount * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{amount:.0f}",
            va="center", fontsize=9
        )

    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150)
    plt.close(fig)
    buffer.seek(0)
    return buffer