# SmartStore AI

A working prototype for supermarket/kirana inventory decision support. It forecasts near-term demand from average daily sales, applies optional weekend and discount uplift, flags stock risks, and recommends reorder quantities. This is an explainable rules-based prototype suitable for demonstrating the product workflow; it is not a trained production ML model.

## Features
- Dashboard with sample inventory
- CSV upload and analysis
- Search and filter by risk status or category
- Batch expiry tracking (when an expiry date is supplied) and estimated shelf-life alerts
- Markdown recommendations for short-dated or slow-moving stock
- Reorder recommendation with supplier lead times
- Purchase-order and reorder action-plan CSV exports
- Weekend, promotion, holiday and local weather scenario factors
- Demand trend chart using uploaded history when available
- Optional critical-stock webhook alerts
- Customer-language demand signal matching for inquiries and complaints
- Anomaly review queue with persisted manager decisions
- Product behavior groups using K-Means when the optional ML dependency is available
- Reorder comparison with manager acceptance and override history
- Perishable stock cap using shelf-life horizon
- `/health` endpoint

## Requirements
- Python 3.10+

## Run
```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
# Optional: Isolation Forest and K-Means model support (standard CPython recommended)
pip install -r requirements-ml.txt
python app.py
```
Open `http://127.0.0.1:5000`.

## CSV format
Required headers:
`product,stock,daily_sales,lead_days,shelf_days,price,discount`

Optional headers are `category`, `expiry_date` (ISO date such as `2026-10-05`), `days_to_expiry`, `supplier`, and `sales_history` (comma- or semicolon-separated daily values; latest seven are charted). Without a batch expiry date, the dashboard labels shelf-life-based expiry as an estimate. Without sales history, its chart uses an illustrative baseline rather than observed transactions.

A ready-to-use file is included at `static/sample_inventory.csv` and can also be downloaded from the app.

## Forecast logic
`forecast/day = daily_sales * discount_factor * weekend_factor * promotion_factor * holiday_factor * weather_factor`. Factors are transparent demo assumptions, not a trained model. Reorder target covers supplier lead time plus two days and safety stock, capped for perishable inventory. Purchase orders are draft CSVs and require supplier confirmation.

To send critical-stock notifications to a compatible webhook, set `SMARTSTORE_ALERT_WEBHOOK_URL` in the environment before starting the app. Alerts are sent when projected stock cover is below 1.5 days. With no webhook configured, the dashboard remains fully usable and displays that alerts are disabled.

## Intelligence views
The Demand Signals view accepts one customer inquiry per line. Product-name/category matches add 10% demand per matched inquiry, capped at 50%; unmatched terms are shown as potential assortment gaps. Anomaly Review applies input validations and, when scikit-learn is installed and there are at least five rows, Isolation Forest. Product Groups uses K-Means with three or more rows; otherwise both features clearly identify their rule-based or insufficient-data mode.

Policy Lab records accepted recommendations and manual reorder quantities in `instance/manager_feedback.sqlite3`. With fewer than three comparable manager decisions, the suggested quantity stays at the rule baseline. After that, the displayed suggestion blends 70% baseline with 30% recent manager quantities for the same category/status. This is a feedback heuristic, not a trained reinforcement-learning or Deep Q-Network model. Data remains local to this app instance.

## Project structure
- `app.py` - Flask server and optimisation logic
- `smartstore_insights.py` - inquiry matching, anomaly/grouping logic and feedback persistence
- `templates/index.html` - dashboard UI
- `static/style.css` - responsive styling
- `static/intelligence.js` and `static/intelligence.css` - intelligence view interactions and styling
- `static/sample_inventory.csv` - sample data
- `requirements.txt` - Python dependency
- `requirements-ml.txt` - optional Isolation Forest and K-Means dependency

## Disclaimer
Use as decision support only. Validate forecasts and purchase quantities against real store data, supplier constraints and business rules before production use.
