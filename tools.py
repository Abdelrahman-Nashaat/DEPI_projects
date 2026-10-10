"""Database tools for ShopAgent-AI.

The functions in this module are intentionally plain Python so they can be
called directly in tests or wrapped with LangChain's ``@tool`` decorator.
All user-provided values in SQL statements are bound parameters.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from db import get_conn

MAX_RESULTS = 5


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Open a database connection, enable FK checks, and always close it."""
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_cart_table(conn: sqlite3.Connection) -> None:
    """Create the lightweight cart table if this database predates carts."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS carts (
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            quantity    INTEGER NOT NULL CHECK (quantity > 0),
            updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (customer_id, product_id)
        )
        """
    )


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def search_products(
    query: str,
    max_price: float | None = None,
    min_rating: float | None = None,
    category: str | None = None,
) -> list[dict]:
    """Search names, categories, brands, and descriptions with optional filters.

    Every query word must match at least one searchable field. Returns at most
    ``MAX_RESULTS`` records, ordered by rating (highest first).
    An empty query returns an empty list rather than the whole catalogue.
    """
    if not isinstance(query, str) or not query.strip():
        return []

    sql = "SELECT id, name, category, price, stock, rating FROM products WHERE 1=1"
    params: list[Any] = []
    for word in query.split():
        # Let each query word match any useful product field. Requiring every
        # word to match at least one field keeps multi-word searches specific
        # while allowing category-, brand-, and description-based discovery.
        sql += (
            " AND (name LIKE ? COLLATE NOCASE"
            " OR category LIKE ? COLLATE NOCASE"
            " OR brand LIKE ? COLLATE NOCASE"
            " OR description LIKE ? COLLATE NOCASE)"
        )
        pattern = f"%{word}%"
        params.extend([pattern, pattern, pattern, pattern])

    if max_price is not None:
        try:
            max_price = float(max_price)
        except (TypeError, ValueError):
            return []
        sql += " AND price <= ?"
        params.append(max_price)

    if min_rating is not None:
        try:
            min_rating = float(min_rating)
        except (TypeError, ValueError):
            return []
        sql += " AND rating >= ?"
        params.append(min_rating)

    if category:
        sql += " AND category LIKE ?"
        params.append(f"%{category}%")

    sql += " ORDER BY rating DESC, id ASC LIMIT ?"
    params.append(MAX_RESULTS)

    with _connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_product_details(product_id: int) -> dict:
    """Return full details for one product, or an ``error`` dictionary."""
    if not _is_positive_int(product_id):
        return {"error": "product_id must be a positive integer"}

    with _connection() as conn:
        row = conn.execute(
            "SELECT id, name, category, brand, price, stock, rating, description "
            "FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()

    if row is None:
        return {"error": f"Product {product_id} not found"}
    return dict(row)


def compare_products(product_ids: list[int]) -> dict:
    """Compare 2–4 distinct products by price, rating, and stock."""
    if not isinstance(product_ids, list) or not 2 <= len(product_ids) <= 4:
        return {"error": "Provide between 2 and 4 product ids"}
    if any(not _is_positive_int(pid) for pid in product_ids):
        return {"error": "Product ids must be positive integers"}
    if len(set(product_ids)) != len(product_ids):
        return {"error": "Provide each product id only once"}

    placeholders = ",".join("?" for _ in product_ids)
    with _connection() as conn:
        rows = conn.execute(
            "SELECT id, name, price, rating, stock FROM products "
            f"WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()

    by_id = {row["id"]: dict(row) for row in rows}
    if len(by_id) != len(product_ids):
        return {"error": "One or more products were not found"}
    products = [by_id[pid] for pid in product_ids]
    cheapest = min(products, key=lambda product: product["price"])
    rated = [product for product in products if product["rating"] is not None]
    best_rated = max(rated, key=lambda product: product["rating"]) if rated else None

    return {
        "products": products,
        "cheapest_id": cheapest["id"],
        "best_rated_id": best_rated["id"] if best_rated else None,
    }


def check_stock(product_id: int, quantity: int = 1) -> dict:
    """Check whether a product has enough stock for the requested quantity."""
    if not _is_positive_int(product_id):
        return {"error": "product_id must be a positive integer"}
    if not _is_positive_int(quantity):
        return {"error": "Quantity must be at least 1"}

    with _connection() as conn:
        row = conn.execute(
            "SELECT id, name, stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()

    if row is None:
        return {"error": f"Product {product_id} not found"}
    return {
        "product_id": row["id"],
        "name": row["name"],
        "available": row["stock"] >= quantity,
        "in_stock": row["stock"],
        "requested": quantity,
    }


def add_to_cart(customer_id: int, product_id: int, quantity: int = 1) -> dict:
    """Add quantity of a product to a customer's cart.

    If the product is already in the cart, ``quantity`` is added to its current
    quantity. Requests exceeding current stock are rejected.
    """
    if not _is_positive_int(customer_id):
        return {"error": "customer_id must be a positive integer"}
    if not _is_positive_int(product_id):
        return {"error": "product_id must be a positive integer"}
    if not _is_positive_int(quantity):
        return {"error": "Quantity must be at least 1"}

    with _connection() as conn:
        _ensure_cart_table(conn)
        customer = conn.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer is None:
            return {"error": f"Customer {customer_id} not found"}

        product = conn.execute(
            "SELECT id, name, price, stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            return {"error": f"Product {product_id} not found"}

        current = conn.execute(
            "SELECT quantity FROM carts WHERE customer_id = ? AND product_id = ?",
            (customer_id, product_id),
        ).fetchone()
        new_quantity = quantity + (current["quantity"] if current else 0)
        if new_quantity > product["stock"]:
            return {
                "error": f"Only {product['stock']} unit(s) of {product['name']} are in stock",
                "product_id": product_id,
                "in_stock": product["stock"],
                "already_in_cart": current["quantity"] if current else 0,
                "requested_additional": quantity,
            }

        conn.execute(
            """INSERT INTO carts (customer_id, product_id, quantity)
               VALUES (?, ?, ?)
               ON CONFLICT(customer_id, product_id) DO UPDATE SET
                   quantity = excluded.quantity,
                   updated_at = CURRENT_TIMESTAMP""",
            (customer_id, product_id, new_quantity),
        )

    return {
        "customer_id": customer_id,
        "product_id": product_id,
        "name": product["name"],
        "quantity": new_quantity,
        "unit_price": product["price"],
        "line_total": round(product["price"] * new_quantity, 2),
        "message": "Product added to cart",
    }


def view_cart(customer_id: int) -> dict:
    """Return all items in a customer's cart and their subtotal."""
    if not _is_positive_int(customer_id):
        return {"error": "customer_id must be a positive integer"}

    with _connection() as conn:
        _ensure_cart_table(conn)
        customer = conn.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer is None:
            return {"error": f"Customer {customer_id} not found"}

        rows = conn.execute(
            """SELECT p.id AS product_id, p.name, p.category, p.price, p.stock,
                      c.quantity
               FROM carts AS c
               JOIN products AS p ON p.id = c.product_id
               WHERE c.customer_id = ?
               ORDER BY p.name COLLATE NOCASE, p.id""",
            (customer_id,),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["line_total"] = round(item["price"] * item["quantity"], 2)
        items.append(item)
    subtotal = round(sum(item["line_total"] for item in items), 2)
    return {
        "customer_id": customer_id,
        "items": items,
        "line_count": len(items),
        "item_count": sum(item["quantity"] for item in items),
        "subtotal": subtotal,
        "total": subtotal,
    }


def create_order(customer_id: int) -> dict:
    """Create an order from the customer's cart and deduct inventory.

    Checkout is atomic: if stock is insufficient, no order is created, no stock
    is deducted, and the cart is preserved so the user can adjust it.
    """
    if not _is_positive_int(customer_id):
        return {"error": "customer_id must be a positive integer"}

    try:
        with _connection() as conn:
            # Lock SQLite for the duration of checkout to avoid overselling.
            conn.execute("BEGIN IMMEDIATE")
            _ensure_cart_table(conn)

            customer = conn.execute(
                "SELECT id, name, email FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            if customer is None:
                return {"error": f"Customer {customer_id} not found"}

            cart_rows = conn.execute(
                """SELECT p.id AS product_id, p.name, p.price, p.stock, c.quantity
                   FROM carts AS c
                   JOIN products AS p ON p.id = c.product_id
                   WHERE c.customer_id = ?
                   ORDER BY p.id""",
                (customer_id,),
            ).fetchall()
            if not cart_rows:
                return {"error": "Cart is empty"}

            for item in cart_rows:
                if item["stock"] < item["quantity"]:
                    return {
                        "error": f"Insufficient stock for {item['name']}",
                        "product_id": item["product_id"],
                        "in_stock": item["stock"],
                        "requested": item["quantity"],
                    }

            total = round(sum(item["price"] * item["quantity"] for item in cart_rows), 2)
            order_cursor = conn.execute(
                "INSERT INTO orders (customer_id, total, status) VALUES (?, ?, ?)",
                (customer_id, total, "confirmed"),
            )
            order_id = int(order_cursor.lastrowid)

            for item in cart_rows:
                update = conn.execute(
                    "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (item["quantity"], item["product_id"], item["quantity"]),
                )
                if update.rowcount != 1:
                    # Roll back any earlier writes in this checkout.
                    conn.rollback()
                    return {"error": "Stock changed during checkout. Please try again."}
                conn.execute(
                    """INSERT INTO order_items (order_id, product_id, quantity, unit_price)
                       VALUES (?, ?, ?, ?)""",
                    (order_id, item["product_id"], item["quantity"], item["price"]),
                )

            conn.execute("DELETE FROM carts WHERE customer_id = ?", (customer_id,))

        return {
            "order_id": order_id,
            "customer_id": customer_id,
            "customer_name": customer["name"],
            "items": [
                {
                    "product_id": item["product_id"],
                    "name": item["name"],
                    "quantity": item["quantity"],
                    "unit_price": item["price"],
                    "line_total": round(item["price"] * item["quantity"], 2),
                }
                for item in cart_rows
            ],
            "total": total,
            "status": "confirmed",
            "message": "Order created successfully",
        }
    except sqlite3.Error as exc:
        return {"error": f"Could not create order: {exc}"}


def _pdf_safe_text(value: Any) -> str:
    """Make database text safe for FPDF's built-in core fonts."""
    text = "" if value is None else str(value)
    replacements = {
        "₹": "INR ",
        "—": "-",
        "–": "-",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def generate_invoice(order_id: int, output_dir: str | Path = "invoices") -> dict:
    """Generate a PDF invoice for an existing order.

    The PDF is written to ``output_dir/invoice_<order_id>.pdf``. The function
    returns the saved path and order total, or an ``error`` dictionary.
    """
    if not _is_positive_int(order_id):
        return {"error": "order_id must be a positive integer"}

    try:
        from fpdf import FPDF
    except ImportError:
        return {"error": "PDF support requires fpdf2; install it with 'pip install fpdf2'"}

    with _connection() as conn:
        order = conn.execute(
            """SELECT o.id, o.customer_id, o.total, o.status, o.created_at,
                      c.name AS customer_name, c.email AS customer_email
               FROM orders AS o
               JOIN customers AS c ON c.id = o.customer_id
               WHERE o.id = ?""",
            (order_id,),
        ).fetchone()
        if order is None:
            return {"error": f"Order {order_id} not found"}
        items = [
            dict(row)
            for row in conn.execute(
                """SELECT p.name, oi.quantity, oi.unit_price,
                          (oi.quantity * oi.unit_price) AS line_total
                   FROM order_items AS oi
                   JOIN products AS p ON p.id = oi.product_id
                   WHERE oi.order_id = ?
                   ORDER BY p.name COLLATE NOCASE""",
                (order_id,),
            ).fetchall()
        ]

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    pdf_path = destination / f"invoice_{order_id}.pdf"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_title(_pdf_safe_text(f"Invoice #{order_id}"))
    pdf.set_author("ShopAgent-AI")

    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, "SHOPAGENT-AI", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, f"INVOICE #{order_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 6, _pdf_safe_text(f"Date: {order['created_at']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _pdf_safe_text(f"Status: {order['status']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Bill to", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 6, _pdf_safe_text(order["customer_name"]), new_x="LMARGIN", new_y="NEXT")
    if order["customer_email"]:
        pdf.cell(0, 6, _pdf_safe_text(order["customer_email"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    widths = (88, 20, 36, 36)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(232, 236, 242)
    for label, width, align in (
        ("Product", widths[0], "L"),
        ("Qty", widths[1], "R"),
        ("Unit price", widths[2], "R"),
        ("Line total", widths[3], "R"),
    ):
        pdf.cell(width, 8, label, border=1, align=align, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", size=9)
    for item in items:
        name = _pdf_safe_text(item["name"])
        if len(name) > 46:
            name = name[:43] + "..."
        pdf.cell(widths[0], 8, name, border=1)
        pdf.cell(widths[1], 8, str(item["quantity"]), border=1, align="R")
        pdf.cell(widths[2], 8, f"{item['unit_price']:.2f}", border=1, align="R")
        pdf.cell(widths[3], 8, f"{item['line_total']:.2f}", border=1, align="R")
        pdf.ln()

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 9, f"Total: {float(order['total']):.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 7, "Thank you for your purchase!", align="C", new_x="LMARGIN", new_y="NEXT")

    try:
        pdf.output(str(pdf_path))
    except (OSError, ValueError) as exc:
        return {"error": f"Could not write invoice PDF: {exc}"}

    return {
        "order_id": order_id,
        "invoice_path": str(pdf_path.resolve()),
        "filename": pdf_path.name,
        "total": float(order["total"]),
        "message": "Invoice generated successfully",
    }
