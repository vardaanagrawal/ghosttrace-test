def export(rows):
    return [(r["customer_email"], r["total_cents"]) for r in rows]
