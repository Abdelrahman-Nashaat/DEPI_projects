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

## CP6: return/warranty RAG and live agent evaluation

The chat agent is defined in `notebook.ipynb`. Its existing product tools now include `answer_policy`, which calls `rag.py` to retrieve a section from `policies/return_policy.md`. The same agent then writes the customer reply with a source name. The policy is example course data: a 30-day return window and a 12-month electronics warranty.

The model routing remains `openai/gpt-oss-20b` for simple requests, `openai/gpt-oss-120b` for complex requests, and Ollama `qwen2.5:3b` as the final fallback. Retrieval uses local multilingual sentence embeddings and FAISS. There is no separate Llama model for RAG.

### Setup and run

Use Python 3.11 or newer and install the existing requirements plus the two RAG packages:

```bash
python -m pip install -r requirements.txt
```

For Groq, copy `.env.example` to `.env` and supply your own `GROQ_API_KEY`. For the local fallback, start Ollama and run `ollama pull qwen2.5:3b`. The embedding model downloads on its first use. Keep real keys in the ignored `.env` file.

Open `notebook.ipynb` in Jupyter or Colab, run the setup and runtime cells in order, then use its Interactive Chat section. The notebook chat exposes product lookup tools and policy retrieval; cart, checkout and invoice functions are available separately in `tools.py`. Product questions need an existing populated `data/shop.db`; `db.init_db()` creates the schema only.

### Evaluate the actual notebook chat

```bash
python evaluate.py --demo
```

`--demo` creates a temporary two-product catalog for the run. To use your existing product database instead, run `python evaluate.py`. The evaluator loads only the notebook cells tagged `shopagent-runtime`, so it runs the same `chat()` used interactively without running the package installer, health checks, old examples or input loop.

Five live questions cover returns and warranties in English and Arabic plus a product search. Checks require the expected tool, retrieved source and key answer facts, and a product ID/price from the catalog. The script prints a passed count and percentage and exits with a nonzero status when any case fails. A reachable Groq or Ollama model is required; this is a small repeatable evaluation of these examples.

The report shows the original model reply separately from the customer reply. For an English policy question answered mostly in Arabic, the chat shows the retrieved English excerpt. If the model omits the source name, the chat appends it from the tool result. The displayed source label therefore does not establish that the model wrote the citation itself.

The policy search returns one section per question. Ask separate questions for multiple policy topics. The original backend tests remain in `test_tools.py`; run them separately with `python -m pytest -q` after installing `pytest`.
