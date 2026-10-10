"""Tests for the ShopAgent-AI database tools. Run with: pytest -q"""
from pathlib import Path
import sqlite3

import pytest

import db
import tools


@pytest.fixture

def shop_db(tmp_path, monkeypatch):
    """Create an isolated SQLite database populated with deterministic samples."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "shop.db"))
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO customers (id, name, email) VALUES (1, 'Test Customer', 'test@example.com')"
        )
        conn.execute(
            "INSERT INTO customers (id, name, email) VALUES (2, 'Second Customer', 'second@example.com')"
        )
        conn.executemany(
            """INSERT INTO products
               (id, name, category, brand, price, stock, description, rating)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (1, "USB Cable", "Electronics", "Acme", 12.50, 10, "USB-A to USB-C cable", 4.7),
                (2, "HDMI Cable", "Electronics", "ViewCo", 18.00, 4, "High-speed HDMI cable", 4.4),
                (3, "Wireless Mouse", "Accessories", "Pointer", 45.00, 5, "Wireless optical mouse", 4.8),
                (4, "Desk Stand", "Accessories", "WorkWell", 22.00, 0, "Metal desk stand", None),
            ],
        )
    yield tmp_path


def test_search_returns_results(shop_db):
    results = tools.search_products("cable")
    assert len(results) == 2
    assert len(results) <= 5


def test_search_respects_max_price(shop_db):
    results = tools.search_products("cable", max_price=15)
    assert all(p["price"] <= 15 for p in results)
    assert [p["id"] for p in results] == [1]


def test_search_respects_min_rating(shop_db):
    results = tools.search_products("cable", min_rating=4.5)
    assert all(p["rating"] >= 4.5 for p in results)
    assert [p["id"] for p in results] == [1]


def test_search_respects_category(shop_db):
    assert tools.search_products("", category="Electronics") == []
    assert all(p["category"] == "Electronics" for p in tools.search_products("cable", category="Electronics"))


def test_search_no_match(shop_db):
    assert tools.search_products("zzzz-not-a-product") == []


def test_search_limits_results(shop_db):
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO products (name, category, price, stock, rating) VALUES (?, ?, ?, ?, ?)",
            [(f"Test item {i}", "Test", 1, 1, 3.0) for i in range(10)],
        )
    assert len(tools.search_products("Test item")) == 5


def test_product_details(shop_db):
    details = tools.get_product_details(1)
    assert details["id"] == 1
    assert details["description"] == "USB-A to USB-C cable"
    assert details["brand"] == "Acme"


def test_product_details_missing(shop_db):
    assert "error" in tools.get_product_details(999999)


def test_compare_products(shop_db):
    result = tools.compare_products([1, 2])
    assert len(result["products"]) == 2
    assert result["cheapest_id"] == 1
    assert result["best_rated_id"] == 1


def test_compare_needs_two_products(shop_db):
    assert "error" in tools.compare_products([1])


def test_compare_rejects_duplicate_ids(shop_db):
    assert "error" in tools.compare_products([1, 1])


def test_check_stock(shop_db):
    result = tools.check_stock(1, quantity=1)
    assert result["product_id"] == 1
    assert result["available"] is True


def test_check_stock_too_much(shop_db):
    assert tools.check_stock(1, quantity=10000)["available"] is False


def test_check_stock_invalid_quantity(shop_db):
    assert "error" in tools.check_stock(1, quantity=0)


def test_add_to_cart_and_view_cart(shop_db):
    first = tools.add_to_cart(1, 1, quantity=2)
    second = tools.add_to_cart(1, 1, quantity=3)
    tools.add_to_cart(1, 2, quantity=1)
    cart = tools.view_cart(1)
    assert first["quantity"] == 2
    assert second["quantity"] == 5
    assert cart["item_count"] == 6
    assert cart["line_count"] == 2
    assert cart["subtotal"] == pytest.approx(80.5)


def test_add_to_cart_rejects_quantity_above_stock(shop_db):
    result = tools.add_to_cart(1, 2, quantity=5)
    assert "error" in result
    assert tools.view_cart(1)["items"] == []


def test_add_to_cart_rejects_unknown_customer_or_product(shop_db):
    assert "error" in tools.add_to_cart(999, 1)
    assert "error" in tools.add_to_cart(1, 999)


def test_view_cart_empty(shop_db):
    cart = tools.view_cart(1)
    assert cart["items"] == []
    assert cart["subtotal"] == 0


def test_create_order_deducts_stock_and_clears_cart(shop_db):
    tools.add_to_cart(1, 1, quantity=2)
    tools.add_to_cart(1, 3, quantity=1)
    order = tools.create_order(1)
    assert order["order_id"] > 0
    assert order["total"] == pytest.approx(70.0)
    assert order["status"] == "confirmed"
    assert tools.check_stock(1)["in_stock"] == 8
    assert tools.check_stock(3)["in_stock"] == 4
    assert tools.view_cart(1)["items"] == []

    with db.get_conn() as conn:
        saved = conn.execute("SELECT total FROM orders WHERE id = ?", (order["order_id"],)).fetchone()
        line_count = conn.execute(
            "SELECT COUNT(*) AS n FROM order_items WHERE order_id = ?", (order["order_id"],)
        ).fetchone()["n"]
    assert saved["total"] == pytest.approx(70.0)
    assert line_count == 2


def test_create_order_rejects_empty_cart(shop_db):
    result = tools.create_order(1)
    assert "error" in result
    assert result["error"] == "Cart is empty"


def test_create_order_preserves_cart_if_stock_changed(shop_db):
    tools.add_to_cart(1, 1, quantity=2)
    with db.get_conn() as conn:
        conn.execute("UPDATE products SET stock = 1 WHERE id = 1")
    result = tools.create_order(1)
    assert "error" in result
    assert tools.view_cart(1)["item_count"] == 2
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_create_order_rejects_unknown_customer(shop_db):
    assert "error" in tools.create_order(999)


def test_generate_invoice_creates_pdf(shop_db):
    tools.add_to_cart(1, 1, quantity=2)
    order = tools.create_order(1)
    invoice = tools.generate_invoice(order["order_id"], output_dir=shop_db / "invoices")
    path = Path(invoice["invoice_path"])
    assert invoice["order_id"] == order["order_id"]
    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF")


def test_generate_invoice_missing_order(shop_db):
    assert "error" in tools.generate_invoice(99999, output_dir=shop_db / "invoices")


def test_generate_invoice_rejects_invalid_id(shop_db):
    assert "error" in tools.generate_invoice(0, output_dir=shop_db / "invoices")


def test_search_matches_category_brand_and_description(shop_db):
    assert [p["id"] for p in tools.search_products("Electronics")] == [1, 2]
    assert [p["id"] for p in tools.search_products("Pointer")] == [3]
    assert [p["id"] for p in tools.search_products("optical mouse")] == [3]


def test_search_matches_each_word_across_product_fields(shop_db):
    # "Wireless" is in the name while "optical" is in the description.
    assert [p["id"] for p in tools.search_products("Wireless optical")] == [3]


def test_add_to_cart_does_not_affect_another_customers_cart(shop_db):
    tools.add_to_cart(1, 1, quantity=2)
    tools.add_to_cart(2, 1, quantity=1)

    assert tools.view_cart(1)["item_count"] == 2
    assert tools.view_cart(2)["item_count"] == 1


def test_create_order_is_atomic_when_any_item_has_insufficient_stock(shop_db):
    tools.add_to_cart(1, 1, quantity=2)
    tools.add_to_cart(1, 2, quantity=3)
    with db.get_conn() as conn:
        conn.execute("UPDATE products SET stock = 2 WHERE id = 2")

    result = tools.create_order(1)

    assert "error" in result
    assert tools.check_stock(1)["in_stock"] == 10
    assert tools.check_stock(2)["in_stock"] == 2
    assert tools.view_cart(1)["item_count"] == 5
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_search_supports_arabic_product_text(shop_db):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO products (id, name, category, price, stock, rating) VALUES (?, ?, ?, ?, ?, ?)",
            (5, "سماعة لاسلكية", "إلكترونيات", 120.0, 3, 4.6),
        )

    assert [p["id"] for p in tools.search_products("سماعة")] == [5]
    assert [p["id"] for p in tools.search_products("إلكترونيات")] == [5]
