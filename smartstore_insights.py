import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

try:
    from sklearn.cluster import KMeans
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    KMeans = IsolationForest = StandardScaler = None
    SKLEARN_AVAILABLE = False


DB_PATH = Path(__file__).parent / 'instance' / 'manager_feedback.sqlite3'
STOP_WORDS = {'a', 'an', 'and', 'for', 'i', 'in', 'is', 'it', 'looking', 'need', 'of', 'the', 'to', 'want', 'with'}


def ensure_store():
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS anomaly_reviews (
            item_id TEXT PRIMARY KEY, decision TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS recommendation_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
            product TEXT NOT NULL, state TEXT NOT NULL, baseline INTEGER NOT NULL,
            recommendation INTEGER NOT NULL, manager_quantity INTEGER NOT NULL,
            decision TEXT NOT NULL, created_at TEXT NOT NULL
        )''')


def item_id(item):
    identity = '|'.join(str(item.get(key, '')) for key in ('product', 'supplier', 'expiry_date'))
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:18]


def _number(item, key, default=0.0):
    try:
        value = float(item.get(key, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _tokens(value):
    return {token for token in re.findall(r'[a-z0-9]+', str(value).lower()) if token not in STOP_WORDS and not token.isdigit()}


def _inquiry_signals(item, inquiries):
    product_tokens = _tokens(' '.join(str(item.get(key, '')) for key in ('product', 'category', 'attributes')))
    matches = []
    for line in inquiries:
        terms = _tokens(line)
        overlap = terms & product_tokens
        if overlap:
            missing = sorted(terms - product_tokens)
            matches.append({'query': line, 'matched_terms': sorted(overlap), 'unmatched_terms': missing})
    return matches


def _basic_anomaly_flags(items):
    flags = {}
    for item in items:
        reasons = []
        for key in ('stock', 'daily_sales', 'lead_days', 'shelf_days', 'price', 'discount'):
            try:
                if not math.isfinite(float(item.get(key, 0))):
                    reasons.append(f'{key} must be a finite number')
            except (TypeError, ValueError):
                reasons.append(f'{key} is not numeric')
        stock = _number(item, 'stock')
        sales = _number(item, 'daily_sales')
        lead = _number(item, 'lead_days', 1)
        shelf = _number(item, 'shelf_days', 1)
        price = _number(item, 'price')
        discount = _number(item, 'discount')
        if stock < 0 or sales < 0 or price < 0:
            reasons.append('Negative stock, sales, or price')
        if lead <= 0 or shelf <= 0:
            reasons.append('Lead or shelf-life days must be positive')
        if discount < 0 or discount > 90:
            reasons.append('Discount is outside the supported 0-90% range')
        if stock > max(1000, sales * 180):
            reasons.append('Stock is unusually high versus daily sales')
        if sales > max(1000, stock * 10):
            reasons.append('Daily sales are unusually high versus stock')
        flags[item_id(item)] = reasons
    if SKLEARN_AVAILABLE and len(items) >= 5:
        features = [[math.log1p(max(0, _number(item, key))) for key in ('stock', 'daily_sales', 'lead_days', 'shelf_days', 'price', 'discount')] for item in items]
        scaled = StandardScaler().fit_transform(features)
        model = IsolationForest(contamination='auto', random_state=17, n_estimators=120)
        predictions = model.fit_predict(scaled)
        for item, prediction in zip(items, predictions):
            if prediction < 0:
                flags[item_id(item)].append('Isolation Forest statistical outlier')
        model_name = 'Isolation Forest'
    else:
        model_name = 'Rule-based checks' if not SKLEARN_AVAILABLE else 'Isolation Forest (needs 5+ rows)'
    return flags, model_name


def _cluster_features(item):
    history = item.get('history') or []
    sales = max(0, _number(item, 'daily_sales'))
    mean = sum(history) / len(history) if history else sales
    variability = pstdev(history) / mean if len(history) > 1 and mean else 0
    momentum = (sum(history[-3:]) / max(1, sum(history[:3]))) if len(history) >= 6 else 1
    promo = max(0, _number(item, 'discount')) / 100
    return [math.log1p(sales), variability, promo, min(3, momentum)]


def _cluster_items(items):
    if not items:
        return [], 'Behavior rules'
    matrix = [_cluster_features(item) for item in items]
    if SKLEARN_AVAILABLE and len(items) >= 3:
        count = min(3, len(items))
        labels = KMeans(n_clusters=count, random_state=17, n_init=10).fit_predict(matrix)
        model_name = 'K-Means'
    else:
        labels = []
        for features in matrix:
            volume, variability, promo, momentum = features
            if variability >= 0.3 or momentum >= 1.35:
                labels.append(2)
            elif promo >= 0.1:
                labels.append(1)
            else:
                labels.append(0 if volume < math.log1p(8) else 1)
        model_name = 'Behavior rules' if not SKLEARN_AVAILABLE else 'K-Means (needs 3+ rows)'
    for item, label, features in zip(items, labels, matrix):
        volume, variability, promo, momentum = features
        if variability >= 0.3 or momentum >= 1.35:
            description = 'Variable demand'
        elif promo >= 0.1:
            description = 'Promo-responsive'
        elif volume < math.log1p(8):
            description = 'Steady / low velocity'
        else:
            description = 'Steady / high velocity'
        item['cluster_id'] = int(label) + 1
        item['cluster_name'] = description
    return items, model_name


def _manager_feedback(item, baseline):
    state = f"{item.get('category', 'Other')}|{item.get('status', 'Healthy')}"
    ensure_store()
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute('SELECT manager_quantity FROM recommendation_feedback WHERE state = ? ORDER BY id DESC LIMIT 30', (state,)).fetchall()
        review = db.execute('SELECT decision FROM anomaly_reviews WHERE item_id = ?', (item_id(item),)).fetchone()
    average = sum(row[0] for row in rows) / len(rows) if rows else baseline
    recommendation = round(0.7 * baseline + 0.3 * average) if len(rows) >= 3 else baseline
    return state, recommendation, len(rows), review[0] if review else 'Pending review'


def enrich_inventory(rows, scenario=None, inquiries=None):
    from app import optimise

    scenario = scenario or {}
    inquiries = [line.strip() for line in (inquiries or []) if str(line).strip()]
    result = []
    for row in rows:
        signals = _inquiry_signals(row, inquiries)
        uplift = min(0.5, len(signals) * 0.1)
        numeric_row = {**row, **{key: _number(row, key) for key in ('stock', 'daily_sales', 'lead_days', 'shelf_days', 'price', 'discount')}}
        item = optimise(numeric_row, {**scenario, 'inquiry_uplift': uplift})
        item['item_id'] = item_id(item)
        item['inquiry_signals'] = signals
        item['inquiry_count'] = len(signals)
        item['inquiry_uplift'] = round(uplift * 100)
        result.append(item)

    anomaly_flags, anomaly_model = _basic_anomaly_flags(rows)
    for item in result:
        reasons = anomaly_flags.get(item['item_id'], [])
        state, rl_recommendation, feedback_count, review_status = _manager_feedback(item, item['reorder'])
        item['anomaly_reasons'] = reasons
        item['anomaly_flagged'] = bool(reasons)
        item['anomaly_model'] = anomaly_model
        item['review_status'] = review_status
        item['baseline_reorder'] = item['reorder']
        item['rl_reorder'] = rl_recommendation
        item['policy_source'] = 'Feedback-adjusted heuristic' if feedback_count >= 3 else 'Baseline (cold start)'
        item['feedback_count'] = feedback_count
        item['feedback_state'] = state

    result, cluster_model = _cluster_items(result)
    for item in result:
        item['cluster_model'] = cluster_model
    return {'items': result, 'anomaly_model': anomaly_model, 'cluster_model': cluster_model}


def review_anomaly(payload):
    ensure_store()
    item = payload.get('item', {})
    decision = payload.get('decision')
    if decision not in ('Verified Incident', 'False Alert'):
        raise ValueError('Decision must be Verified Incident or False Alert.')
    with sqlite3.connect(DB_PATH) as db:
        db.execute('INSERT INTO anomaly_reviews(item_id, decision, updated_at) VALUES(?,?,?) ON CONFLICT(item_id) DO UPDATE SET decision=excluded.decision, updated_at=excluded.updated_at',
                   (item_id(item), decision, datetime.now(timezone.utc).isoformat()))
    return {'item_id': item_id(item), 'decision': decision}


def log_recommendation(payload):
    ensure_store()
    item = payload.get('item', {})
    decision = payload.get('decision')
    if decision not in ('Accepted', 'Manual override'):
        raise ValueError('Decision must be Accepted or Manual override.')
    baseline = max(0, int(payload.get('baseline', 0)))
    recommendation = max(0, int(payload.get('recommendation', baseline)))
    manager_quantity = max(0, int(payload.get('manager_quantity', recommendation)))
    state = str(payload.get('state') or f"{item.get('category', 'Other')}|{item.get('status', 'Healthy')}")[:100]
    with sqlite3.connect(DB_PATH) as db:
        db.execute('INSERT INTO recommendation_feedback(item_id, product, state, baseline, recommendation, manager_quantity, decision, created_at) VALUES(?,?,?,?,?,?,?,?)',
                   (item_id(item), str(item.get('product', ''))[:200], state, baseline, recommendation, manager_quantity, decision, datetime.now(timezone.utc).isoformat()))
    return {'logged': True, 'decision': decision, 'manager_quantity': manager_quantity}


def recent_feedback(limit=30):
    ensure_store()
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT product, baseline, recommendation, manager_quantity, decision, created_at FROM recommendation_feedback ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(row) for row in rows]