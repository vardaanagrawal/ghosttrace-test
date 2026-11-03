from services import payment


def checkout(order):
    return payment.charge(order)


def customer_contact(row):
    return row["customer_email"]
