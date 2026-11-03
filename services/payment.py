from config import settings


def charge(order):
    return call_gateway(order, timeout=settings.PAYMENT_TIMEOUT)


def call_gateway(order, timeout):
    return {"order": order, "timeout": timeout, "status": "ok"}
