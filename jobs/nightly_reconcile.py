from config.settings import PAYMENT_TIMEOUT

# Reconciles ~10,000 orders one by one in the 02:00-08:00 settlement window.
assert PAYMENT_TIMEOUT * 10000 < 21600, "reconcile must finish inside the 6h window"
