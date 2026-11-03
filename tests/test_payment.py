from services import payment


def test_charge_returns_ok():
    assert payment.charge("o1")["status"] == "ok"
