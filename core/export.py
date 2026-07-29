import csv
import io


def build_csv(rows: list) -> str:
    """
    Convert raw transaction rows into CSV text with a header row.
    Tolerates rows with missing trailing columns.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Category", "Amount", "Description"])
    for row in rows:
        padded = list(row) + [""] * (5 - len(row))
        writer.writerow(padded[:5])
    return output.getvalue()