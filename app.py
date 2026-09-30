from flask import Flask, render_template, request, jsonify, Response
import csv, io, json, math, os, urllib.request
from datetime import date, datetime
from smartstore_insights import enrich_inventory, log_recommendation, recent_feedback, review_anomaly

app = Flask(__name__)

SAMPLE = [
 {"product":"Milk 1L","stock":28,"daily_sales":12,"lead_days":2,"shelf_days":5,"price":62,"discount":0},
 {"product":"Butter 500g","stock":16,"daily_sales":4,"lead_days":3,"shelf_days":30,"price":285,"discount":5},
 {"product":"Orange Juice 1L","stock":10,"daily_sales":7,"lead_days":2,"shelf_days":12,"price":110,"discount":10},
 {"product":"Rice 5kg","stock":42,"daily_sales":5,"lead_days":4,"shelf_days":365,"price":390,"discount":0},
]

def category_for(row):
    if row.get('category'):
        return str(row['category']).strip()
    product = str(row.get('product', '')).lower()
    if any(word in product for word in ('milk', 'butter', 'cheese', 'yogurt', 'yoghurt')):
        return 'Dairy'
    if any(word in product for word in ('juice', 'water', 'drink', 'soda', 'tea', 'coffee')):
        return 'Beverages'
    if any(word in product for word in ('rice', 'flour', 'pasta', 'sugar', 'oil', 'bread')):
        return 'Staples'
    return 'Other'


def optimise(row, scenario=None):
    scenario = scenario or {}
    sales=max(0,float(row.get('daily_sales',0))); stock=max(0,float(row.get('stock',0)))
    lead=max(1,float(row.get('lead_days',1))); shelf=max(1,float(row.get('shelf_days',365)))
    discount=max(0,min(90,float(row.get('discount',0))))
    category = category_for(row)
    promo = max(0, min(100, float(scenario.get('promo_percent', 0))))
    weather = scenario.get('weather', 'none')
    weather_factors = {
        'heatwave': {'Beverages': 1.25, 'Dairy': 1.05},
        'rain': {'Beverages': 0.9, 'Staples': 1.05},
    }
    weather_factor = weather_factors.get(weather, {}).get(category, 1.0)
    # Scenario factors are transparent estimates, not a trained demand model.
    factor = ((1 + discount / 200.0) * (1.18 if scenario.get('weekend') else 1.0)
              * (1 + promo / 100.0) * (1.25 if scenario.get('holiday') else 1.0)
              * weather_factor * (1 + max(0, min(0.5, float(scenario.get('inquiry_uplift', 0))))))
    forecast=sales*factor
    safety=max(1,forecast*0.35)
    target=forecast*(lead+2)+safety
    # Perishables are capped to a conservative sell-through horizon.
    target=min(target, forecast*max(1,min(shelf, lead+4)))
    reorder=max(0,math.ceil(target-stock))
    days_cover=stock/forecast if forecast else 999
    expiry_value = row.get('days_to_expiry')
    expiry_days = max(0, float(expiry_value)) if expiry_value not in (None, '') else shelf
    expiry_date = row.get('expiry_date')
    if expiry_date:
        expiry_days = max(0, (date.fromisoformat(str(expiry_date)[:10]) - date.today()).days)
    if expiry_days <= 3 and stock > forecast * max(1, expiry_days): status='Wastage risk'
    elif days_cover < lead: status='Understock risk'
    elif shelf <= 14 and stock > forecast*shelf: status='Wastage risk'
    elif days_cover > lead+6: status='Overstock risk'
    else: status='Healthy'
    markdown = 15 if expiry_days <= 3 or days_cover > lead + 6 else (10 if days_cover > lead + 3 else 0)
    raw_history = row.get('sales_history', [])
    if isinstance(raw_history, str):
        raw_history = raw_history.replace(';', ',').split(',')
    try:
        history = [round(max(0, float(value)), 1) for value in raw_history if str(value).strip()][-7:]
    except (TypeError, ValueError):
        history = []
    has_sales_history = bool(history)
    if not has_sales_history:
        history = [round(sales * (0.82 + ((i * 17) % 9) / 100), 1) for i in range(7)]
    projection = [round(forecast * (1 + (i - 3) * 0.015), 1) for i in range(7)]
    return {
        **row, 'category': category, 'forecast': round(forecast, 1), 'reorder': reorder,
        'days_cover': round(days_cover, 1), 'status': status,
        'days_to_expiry': round(expiry_days, 1), 'markdown_percent': markdown,
        'history': history, 'projection': projection,
        'history_source': 'Actual sales history' if has_sales_history else 'Illustrative baseline',
    }


def csv_response(rows, columns, filename):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return Response(output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': f'attachment; filename={filename}'
    })

@app.route('/')
def index():
    analysis = enrich_inventory(SAMPLE)
    return render_template('index.html', items=analysis['items'], anomaly_model=analysis['anomaly_model'], cluster_model=analysis['cluster_model'])

@app.route('/analyse', methods=['POST'])
def analyse():
    scenario = {
        'weekend': request.form.get('weekend') == 'on',
        'holiday': request.form.get('holiday') == 'on',
        'promo_percent': request.form.get('promo_percent', 0),
        'weather': request.form.get('weather', 'none'),
    }
    f=request.files.get('file')
    if not f: return jsonify({'error':'Please choose a CSV file.'}),400
    try:
        text=f.read().decode('utf-8-sig'); rows=list(csv.DictReader(io.StringIO(text)))
        required={'product','stock','daily_sales','lead_days','shelf_days','price','discount'}
        if not rows or not required.issubset(rows[0].keys()):
            return jsonify({'error':'CSV columns must be: '+', '.join(sorted(required))}),400
        inquiries = request.form.get('customer_inquiries', '').splitlines()
        analysis = enrich_inventory(rows, scenario, inquiries)
        return jsonify(analysis)
    except Exception as e:
        return jsonify({'error':'Could not read CSV: '+str(e)}),400


@app.route('/scenario', methods=['POST'])
def scenario_forecast():
    payload = request.get_json(force=True)
    try:
        return jsonify(enrich_inventory(payload.get('items', []), payload.get('scenario', {}), payload.get('inquiries', [])))
    except (TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid inventory or scenario value: {e}'}), 400


@app.route('/review/anomaly', methods=['POST'])
def save_anomaly_review():
    try:
        return jsonify(review_anomaly(request.get_json(force=True)))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/review/recommendation', methods=['POST'])
def save_recommendation_feedback():
    try:
        return jsonify(log_recommendation(request.get_json(force=True)))
    except (TypeError, ValueError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/review/history')
def recommendation_history():
    return jsonify({'items': recent_feedback()})


@app.route('/export/action-plan', methods=['POST'])
def export_action_plan():
    items = request.get_json(force=True).get('items', [])
    rows = [item for item in items if int(item.get('reorder', 0)) > 0]
    return csv_response(rows, ['product', 'category', 'status', 'stock', 'forecast', 'days_cover', 'lead_days', 'reorder'], 'smartstore-action-plan.csv')


@app.route('/export/purchase-order', methods=['POST'])
def export_purchase_order():
    items = request.get_json(force=True).get('items', [])
    rows = []
    for item in items:
        quantity = int(item.get('reorder', 0))
        if quantity:
            price = float(item.get('price', 0))
            rows.append({**item, 'order_qty': quantity, 'estimated_cost': round(price * quantity, 2),
                         'supplier': item.get('supplier') or 'Supplier to confirm', 'requested_on': date.today().isoformat()})
    return csv_response(rows, ['product', 'supplier', 'order_qty', 'price', 'estimated_cost', 'lead_days', 'requested_on'], 'smartstore-purchase-order.csv')


@app.route('/alerts', methods=['POST'])
def send_alerts():
    webhook_url = os.environ.get('SMARTSTORE_ALERT_WEBHOOK_URL')
    if not webhook_url:
        return jsonify({'sent': False, 'configured': False})
    alerts = request.get_json(force=True).get('items', [])
    alerts = [item for item in alerts if float(item.get('days_cover', 999)) < 1.5]
    if not alerts:
        return jsonify({'sent': False, 'configured': True})
    message = 'SmartStore critical stock: ' + ', '.join(
        f"{item.get('product')} ({item.get('days_cover')} days cover)" for item in alerts
    )
    body = json.dumps({'text': message, 'alerts': alerts}).encode('utf-8')
    req = urllib.request.Request(webhook_url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            return jsonify({'sent': response.status < 300, 'configured': True})
    except Exception as e:
        return jsonify({'sent': False, 'configured': True, 'error': str(e)}), 502

@app.route('/health')
def health(): return {'status':'ok','time':datetime.utcnow().isoformat()+'Z'}

if __name__=='__main__': app.run(host='127.0.0.1',port=5000,debug=True)
