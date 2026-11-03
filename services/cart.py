from config.settings import MAX_CART_ITEMS


def can_add(cart):
    return len(cart) < MAX_CART_ITEMS
