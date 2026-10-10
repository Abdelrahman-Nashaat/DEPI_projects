# ShopAgent-AI tools update

## Implemented MVP tools

- `search_products`: search every query word across product name, category, brand, and description; supports price, minimum rating, and category filters.
- `get_product_details`: return product details by ID.
- `compare_products`: compare 2–4 distinct products and report cheapest and best-rated IDs.
- `check_stock`: validate requested quantity against current inventory.
- `add_to_cart` / `view_cart`: customer-specific cart operations with quantity and stock validation.
- `create_order`: create an order from the cart, atomically deduct inventory, store order items, and clear the cart after successful checkout.
- `generate_invoice`: generate a PDF invoice for a saved order.

## Validation

Run tests from this directory:

```bash
python -m compileall -q tools.py db.py test_tools.py
pytest -q
```

The local validation run completed successfully: **30 passed**.

`db.py` in this bundle is the lightweight SQLite schema/test harness used for reproducible tests. Keep your repository's existing database configuration unless you intentionally want to adopt this harness.
