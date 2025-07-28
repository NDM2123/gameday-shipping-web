import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from static_data import get_zone_from_vendor_zip, get_shipping_cost
from google_sheets import get_item_names, get_item_weight, add_item_to_sheet, remove_item_from_sheet, save_shipping_history, get_shipping_history, delete_item_shipping_history
from google_sheets import get_vendors_data, add_vendor_to_sheet
import math

app = Flask(__name__)

OFFSET_PERCENT = 0.14  # 14% markup

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/api/item_names", methods=["GET"])
def api_item_names():
    try:
        item_names = get_item_names()
        return jsonify({"items": item_names})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/item_weight", methods=["POST"])
def api_item_weight():
    data = request.json or {}
    name = data.get("name", "").lower().strip()
    try:
        weight = get_item_weight(name)
        if weight is None:
            return jsonify({"error": f"Weight not found for '{name}'"}), 400
        return jsonify({"weight": weight})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/add_item", methods=["POST"])
def api_add_item():
    data = request.json or {}
    name = data.get("name", "").strip()
    weight = data.get("weight", None)
    if not name or weight is None:
        return jsonify({"error": "Name and weight are required."}), 400
    try:
        add_item_to_sheet(name, weight)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/remove_item", methods=["POST"])
def api_remove_item():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400
    try:
        success = remove_item_from_sheet(name)
        if not success:
            return jsonify({"error": "Item not found."}), 400
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    data = request.json or {}
    vendor_zip = data.get("vendor_zip", "")
    # Pad vendor_zip with leading zero if only 4 digits
    if vendor_zip and len(vendor_zip) == 4 and vendor_zip.isdigit():
        vendor_zip = '0' + vendor_zip
    receiving_zip = data.get("receiving_zip", "")
    items = data.get("items", [])
    vendor_label = data.get("vendor_label", None)  # new: pass vendor label if available
    po_number = data.get("po_number", "").strip()  # new: get PO number
    try:
        # Correct order: destination (vendor_zip), origin (receiving_zip)
        zone = get_zone_from_vendor_zip(vendor_zip, receiving_zip)
        total_weight = sum(float(item.get("weight") or 0.0) * float(item.get("quantity") or 0.0) for item in items)
        total_shipping_cost = float(get_shipping_cost(zone, total_weight) or 0.0)
        offset_shipping_cost = float(total_shipping_cost * (1 + OFFSET_PERCENT))
        item_total = sum(float(item.get("weight") or 0.0) * float(item.get("quantity") or 0.0) for item in items)
        result = {
            "total_weight": total_weight,
            "zone": zone,
            "offset_shipping_cost": offset_shipping_cost,
            "items": []
        }
        for item in items:
            weight = float(item.get("weight") or 0.0)
            quantity = int(item.get("quantity") or 0)
            cost = float(item.get("cost") or 0.0)
            vendor = item.get("vendor", vendor_label or "")
            safe_total_weight = float(total_weight or 0.0)
            weight_share = (weight * quantity / safe_total_weight) if safe_total_weight else 0.0
            offset_item_cost = weight_share * offset_shipping_cost
            offset_cost_per_unit = offset_item_cost / quantity if quantity else 0.0
            # Retail price suggestions
            retail_50 = (cost + offset_cost_per_unit) / 0.5 if quantity else 0.0
            retail_55 = (cost + offset_cost_per_unit) / 0.45 if quantity else 0.0
            retail_60 = (cost + offset_cost_per_unit) / 0.4 if quantity else 0.0
            # Append to history (now with vendor, UPS flag, weight used, and PO number)
            save_shipping_history(item["name"], offset_cost_per_unit, offset_cost_per_unit, quantity, vendor, is_ups='Yes', weight_used=weight, po_number=po_number)
            item_result = {
                "name": item["name"],
                "quantity": int(quantity),
                "weight_per_unit": weight,
                "offset_shipping_per_unit": offset_cost_per_unit,
                "cost": cost,
                "retail_50": retail_50,
                "retail_55": retail_55,
                "retail_60": retail_60,
                "vendor": vendor
            }
            result["items"].append(item_result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/item_shipping_averages", methods=["GET"])
def api_item_shipping_averages():
    try:
        history = get_shipping_history()
        if not history:
            return jsonify({"items": []})
        # Group by (item name, vendor) and calculate weighted averages
        item_averages = {}
        for record in history:
            item_name = record['Item Name']
            vendor = record.get('Vendor', '')
            key = (item_name, vendor)
            quantity = record.get('Quantity', 1)
            try:
                quantity = float(quantity)
            except Exception:
                quantity = 1
            offset_cost = record['Per-Unit Shipping Cost (Offset)']
            is_ups = record.get('UPS', 'No') # Get UPS value
            if key not in item_averages:
                item_averages[key] = {
                    'offset_cost_sum': 0.0,
                    'quantity_sum': 0.0,
                    'UPS': is_ups # Store UPS value
                }
            item_averages[key]['offset_cost_sum'] += float(offset_cost) * quantity
            item_averages[key]['quantity_sum'] += quantity
        # Calculate weighted averages
        items = []
        for (item_name, vendor), data in item_averages.items():
            if data['quantity_sum'] > 0:
                avg_offset_cost = data['offset_cost_sum'] / data['quantity_sum']
            else:
                avg_offset_cost = 0.0
            items.append({
                "name": item_name,
                "vendor": vendor,
                "avg_per_unit_shipping_offset": avg_offset_cost,
                "UPS": data['UPS'] # Include UPS value
            })
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/delete_item_shipping_history", methods=["POST"])
def api_delete_item_shipping_history():
    data = request.json or {}
    name = data.get("name", "").strip()
    vendor = data.get("vendor", "").strip() # Added vendor parameter
    if not name:
        return jsonify({"error": "Name is required."}), 400
    try:
        success = delete_item_shipping_history(name, vendor) # Pass vendor
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/add_non_ups_item", methods=["POST"])
def api_add_non_ups_item():
    data = request.json or {}
    name = data.get("name", "").strip()
    quantity = data.get("quantity", None)
    freight = data.get("freight", None)
    vendor = data.get("vendor", "").strip() # Added vendor parameter
    weight_used = data.get("weight_used", None)
    if not name or quantity is None or freight is None or not vendor:
        return jsonify({"error": "All fields are required."}), 400
    try:
        quantity = int(quantity)
        freight = float(freight)
        if quantity <= 0 or freight <= 0:
            return jsonify({"error": "Quantity and freight must be positive."}), 400
        per_unit_cost = freight / quantity
        # Save with per_unit_cost in both actual and offset fields, UPS flag 'No', and weight used
        success = save_shipping_history(name, per_unit_cost, per_unit_cost, quantity, vendor, is_ups='No', weight_used=weight_used)
        if not success:
            return jsonify({"error": "Failed to save to shipping history"}), 500
        return jsonify({"success": True})
    except Exception as e:
        print(f"Error in api_add_non_ups_item: {e}")
        return jsonify({"error": f"Server error: {str(e)}"}), 400

@app.route("/api/items_with_weights", methods=["GET"])
def api_items_with_weights():
    from google_sheets import get_items_with_weights
    items = get_items_with_weights()
    return jsonify({"items": items})

@app.route("/api/item_names_by_vendor", methods=["POST"])
def api_item_names_by_vendor():
    data = request.json or {}
    vendor = data.get("vendor", "").strip()
    from google_sheets import get_shipping_history
    history = get_shipping_history()
    items = sorted(list(set(
        record['Item Name'] for record in history if record.get('Vendor', '').strip() == vendor and record.get('Item Name')
    )))
    return jsonify({"items": items})

@app.route('/all_gsf_classic_black_punched_out_400x.webp')
def serve_logo():
    return send_from_directory('assets', 'all_gsf_classic_black_punched_out_400x.webp')

@app.route("/api/last_weight_used", methods=["POST"])
def api_last_weight_used():
    data = request.json or {}
    item_name = data.get("item_name", "").strip()
    vendor = data.get("vendor", "").strip()
    from google_sheets import get_last_weight_used
    weight = get_last_weight_used(item_name, vendor)
    return jsonify({"weight": weight})

# New API endpoint for vendor list
@app.route("/api/vendors", methods=["GET"])
def api_vendors():
    try:
        vendors = get_vendors_data()
        return jsonify({"vendors": vendors})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# New API endpoint to add a vendor
@app.route("/api/add_vendor", methods=["POST"])
def api_add_vendor():
    data = request.json or {}
    name = data.get("name", "").strip()
    zip_code = data.get("zip", "").strip()
    if not name or not zip_code:
        return jsonify({"error": "Vendor name and ZIP code are required."}), 400
    try:
        add_vendor_to_sheet(name, zip_code)
        return jsonify({"success": True})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

HTML_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gameday Shipping Cost Estimator</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css" rel="stylesheet" />
    <style>
        :root {
            --illini-blue: #13294B;
            --illini-orange: #FF552E;
        }
        body { background: var(--illini-blue); }
        body { position: relative; left: -75px; }
        .main-flex {
            display: flex;
            flex-direction: row;
            justify-content: center;
            align-items: flex-start;
            min-height: 100vh;
            position: relative;
        }
        .main-center-wrap {
            display: flex;
            flex-direction: row;
            align-items: flex-start;
            justify-content: center;
            position: relative;
        }
        .container {
            max-width: 700px;
            margin-top: 40px;
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 4px 24px rgba(19,41,75,0.15);
            z-index: 2;
        }
        .uiuc-header {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
        }
        .uiuc-logo {
            height: 60px;
        }
        .uiuc-title {
            color: var(--illini-blue);
            font-weight: 700;
            font-size: 2rem;
            margin: 0;
        }
        .item-list { min-height: 60px; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; padding: 10px; margin-bottom: 10px; }
        .result-box { background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; padding: 15px; margin-top: 20px; }
        .select2-container { width: 100% !important; }
        .btn-primary, .btn-success { background: var(--illini-orange); border-color: var(--illini-orange); }
        .btn-primary:hover, .btn-success:hover { background: #e64a19; border-color: #e64a19; }
        .form-label { color: var(--illini-blue); font-weight: 600; }
        hr { border-top: 2px solid var(--illini-blue); }
        /* New item box styling */
        .add-item-box {
            width: 220px;
            background: #f4f6fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 16px 12px 12px 12px;
            position: absolute;
            left: calc(50% - 350px - 220px - 50px); /* 350px is half the container width, 220px is add-item width, 50px gap */
            top: 40px;
            box-shadow: 0 2px 8px rgba(19,41,75,0.08);
            font-size: 0.95rem;
            z-index: 3;
        }
        .remove-item-box {
            width: 220px;
            background: #f4f6fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 16px 12px 12px 12px;
            margin-top: 70px;
            position: absolute;
            left: calc(50% - 350px - 220px - 50px);
            top: 220px;
            box-shadow: 0 2px 8px rgba(19,41,75,0.08);
            font-size: 0.95rem;
            z-index: 3;
        }
        @media (max-width: 1100px) {
            .add-item-box {
                position: static;
                left: 0;
                top: 0;
                margin-bottom: 16px;
                width: 100%;
            }
            .remove-item-box {
                position: static;
                left: 0;
                top: 0;
                margin-bottom: 16px;
                width: 100%;
            }
            .main-center-wrap {
                flex-direction: column;
                align-items: stretch;
            }
            .container {
                margin-left: 0;
                margin-right: 0;
            }
        }
    </style>
</head>
<body>
<div class="main-flex">
  <div class="main-center-wrap">
    <div class="add-item-box">
        <div style="color:#13294B; margin-bottom:8px;"><b>Add New Item</b></div>
        <form id="add-item-form">
            <div class="mb-2">
                <label for="new_item_name" class="form-label" style="font-size:0.95em;">Item Name</label>
                <input type="text" class="form-control form-control-sm" id="new_item_name" maxlength="50" required>
            </div>
            <div class="mb-2">
                <label for="new_item_weight" class="form-label" style="font-size:0.95em;">Weight (lbs)</label>
                <input type="number" class="form-control form-control-sm" id="new_item_weight" min="0.01" step="0.01" required>
            </div>
            <button type="submit" class="btn btn-sm btn-primary w-100">Add Item</button>
            <div id="add-item-msg" style="font-size:0.9em; margin-top:6px;"></div>
        </form>
    </div>
    <div class="remove-item-box">
        <div style="color:#13294B; margin-bottom:8px;"><b>Remove Item</b></div>
        <form id="remove-item-form">
            <div class="mb-2">
                <label for="remove_item_name" class="form-label" style="font-size:0.95em;">Item Name</label>
                <select class="form-control form-control-sm" id="remove_item_name" style="width:100%"></select>
            </div>
            <button type="submit" class="btn btn-sm btn-danger w-100">Remove Item</button>
            <div id="remove-item-msg" style="font-size:0.9em; margin-top:6px;"></div>
        </form>
    </div>
    <!-- Add Vendor Box -->
    <div class="remove-item-box" style="top: 400px;">
        <div style="color:#13294B; margin-bottom:8px;"><b>Add Vendor</b></div>
        <form id="add-vendor-form">
            <div class="mb-2">
                <label for="vendor_name_input" class="form-label" style="font-size:0.95em;">Vendor Name</label>
                <input type="text" class="form-control form-control-sm" id="vendor_name_input" maxlength="100" required>
            </div>
            <div class="mb-2">
                <label for="vendor_zip_input" class="form-label" style="font-size:0.95em;">Vendor ZIP Code</label>
                <input type="text" class="form-control form-control-sm" id="vendor_zip_input" maxlength="10" required pattern="\\d{5}">
            </div>
            <button type="submit" class="btn btn-sm btn-primary w-100">Add Vendor</button>
            <div id="add-vendor-msg" style="font-size:0.9em; margin-top:6px;"></div>
        </form>
    </div>
    <div class="container shadow p-4 bg-white rounded">
    <div class="uiuc-header">
        <img src="/all_gsf_classic_black_punched_out_400x.webp" alt="Logo" class="uiuc-logo">
        <h2 class="uiuc-title">Gameday Shipping Cost Estimator</h2>
    </div>
    <form id="shipping-form">
          <div class="row mb-3">
              <div class="col-12">
                  <button id="show-non-ups-calc" class="btn btn-warning mb-3" type="button">Add Non UPS Item</button>
              </div>
          </div>
        <div class="row mb-3">
            <div class="col">
                <label for="vendor_zip" class="form-label">Vendor ZIP</label>
                  <select class="form-control" id="vendor_zip" style="width:100%"></select>
            </div>
            <div class="col">
                <label for="receiving_zip" class="form-label">Receiving Warehouse</label>
                <select class="form-control" id="receiving_zip" style="width:100%">
                  <option value="61801">Illinois</option>
                  <option value="47401">Indiana</option>
                  <option value="47303">Ball State</option>
                  <option value="60202">Northwestern</option>
                  <option value="45701">Ohio State</option>
                </select>
            </div>
            <div class="col">
                <label for="po_number" class="form-label">PO Number (Optional)</label>
                <input type="text" class="form-control" id="po_number" placeholder="Enter PO number">
            </div>
        </div>
        <div class="row mb-3">
            <div class="col-6">
                <label for="item_name" class="form-label">Item Name</label>
                <select class="form-control" id="item_name" style="width:100%"></select>
            </div>
            <div class="col-3">
                <label for="item_quantity" class="form-label">Quantity</label>
                <input type="number" class="form-control" id="item_quantity">
            </div>
              <div class="col-3">
                  <label for="item_cost" class="form-label">Item Cost ($)</label>
                  <input type="number" class="form-control" id="item_cost" min="0.01" step="0.01">
              </div>
              <div class="col-12 d-flex align-items-end mt-2">
                <button type="button" class="btn btn-primary w-100" id="add-item-btn">Add Item</button>
            </div>
        </div>
        <div class="item-list" id="item-list"></div>
        <div class="d-flex justify-content-between">
            <button type="submit" class="btn btn-success">Calculate Shipping</button>
            <button type="button" class="btn btn-secondary" id="clear-items-btn">Clear All Items</button>
        </div>
    </form>
    <div class="result-box mt-4" id="result-box" style="display:none;"></div>
      <!-- Non UPS Calculator (hidden by default) -->
      <div id="non-ups-calc-wrap" style="display:none;">
        <button id="close-non-ups-calc" class="btn btn-secondary mb-3" type="button">Back to Main Calculator</button>
        <form id="non-ups-calc-form">
          <div class="row mb-3">
            <div class="col">
              <label for="non_ups_vendor_zip" class="form-label">Vendor</label>
              <select class="form-control" id="non_ups_vendor_zip" style="width:100%"></select>
            </div>
          </div>
          <div id="non-ups-items-list" class="mb-3"></div>
          <div class="row mb-3">
            <div class="col-6">
              <label for="non_ups_item_name" class="form-label">Item Name</label>
              <input type="text" class="form-control" id="non_ups_item_name">
            </div>
            <div class="col-3">
              <label for="non_ups_item_quantity" class="form-label">Quantity</label>
              <input type="number" class="form-control" id="non_ups_item_quantity">
            </div>
            <div class="col-3">
              <label for="non_ups_item_weight" class="form-label">Weight (lbs)</label>
              <select class="form-control" id="non_ups_item_weight"></select>
            </div>
          </div>
          <div class="mb-3">
            <button type="button" class="btn btn-primary w-100" id="add-non-ups-item-btn">Add Item</button>
          </div>
          <div class="mb-3">
            <label for="non_ups_freight_total" class="form-label">Total Freight Invoiced ($)</label>
            <input type="number" class="form-control" id="non_ups_freight_total" min="0.01" step="0.01" required>
          </div>
          <div class="d-flex justify-content-between">
            <button type="submit" class="btn btn-success">Calculate Freight Split</button>
            <button type="button" class="btn btn-secondary" id="clear-non-ups-items-btn">Clear All Items</button>
          </div>
        </form>
        <div class="result-box mt-4" id="non-ups-result-box" style="display:none;"></div>
      </div>
</div>
    <!-- New right-side averages panel -->
    <div id="averages-panel" style="position: fixed; top: 40px; left: calc(50% + 350px + 50px - 75px); bottom: 50px; width: 395px; background: #f4f6fa; border: 1px solid #dee2e6; border-radius: 8px; box-shadow: 0 2px 8px rgba(19,41,75,0.08); padding: 18px 14px 14px 14px; z-index: 4; display: flex; flex-direction: column;">
      <div style="color:#13294B; font-weight:700; font-size:1.1em; margin-bottom:10px;">Avg. Per-Unit Shipping Costs</div>
      <input type="text" id="averages-search" class="form-control form-control-sm mb-2" placeholder="Search item or vendor..." style="font-size:0.97em;">
      <div style="flex: 1 1 auto; min-height: 0; overflow-y: auto;">
        <table class="table table-sm table-bordered bg-white" style="font-size:0.97em; margin-bottom:0;">
          <thead>
            <tr style="background:#e3e8f0;">
              <th>Item</th>
              <th>Vendor</th>
              <th>Per-Unit Shipping</th>
              <th>UPS</th>
            </tr>
          </thead>
          <tbody id="averages-table-body">
            <tr><td colspan="3" class="text-center text-muted">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js"></script>
<script>
let items = [];

// Keyboard navigation function for Enter key
function setupKeyboardNavigation() {
    // Define the tab order for UPS calculator
    const upsTabOrder = [
        '#vendor_zip',
        '#receiving_zip', 
        '#po_number',
        '#item_name',
        '#item_quantity',
        '#item_cost',
        '#add-item-btn'
    ];
    
    // Define the tab order for non-UPS calculator
    const nonUpsTabOrder = [
        '#non_ups_vendor_zip',
        '#non_ups_item_name',
        '#non_ups_item_quantity',
        '#non_ups_item_weight',
        '#add-non-ups-item-btn',
        '#non_ups_freight_total'
    ];
    
    // Handle Enter key press
    $(document).on('keydown', 'input, select', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            
            // Determine which calculator is active
            let currentTabOrder;
            if ($('#shipping-form').is(':visible')) {
                currentTabOrder = upsTabOrder;
            } else if ($('#non-ups-calc-wrap').is(':visible')) {
                currentTabOrder = nonUpsTabOrder;
            } else {
                return; // No calculator active
            }
            
            // Find current element in tab order
            const currentIndex = currentTabOrder.indexOf('#' + this.id);
            if (currentIndex === -1) return;
            
            // Find next element
            let nextElement = null;
            for (let i = currentIndex + 1; i < currentTabOrder.length; i++) {
                const nextSelector = currentTabOrder[i];
                const $next = $(nextSelector);
                if ($next.length && $next.is(':visible')) {
                    nextElement = $next;
                    break;
                }
            }
            
            // Focus next element or trigger button click
            if (nextElement) {
                if (nextElement.is('button')) {
                    nextElement.click();
                } else {
                    nextElement.focus();
                    // For select2 elements, open the dropdown
                    if (nextElement.hasClass('select2-hidden-accessible')) {
                        nextElement.select2('open');
                    }
                }
            }
        }
    });
    
    // Special handling for select2 dropdowns
    $(document).on('select2:select', '.select2-hidden-accessible', function(e) {
        // After selecting an item, move to next field
        setTimeout(() => {
            const currentId = '#' + this.id;
            let currentTabOrder;
            if ($('#shipping-form').is(':visible')) {
                currentTabOrder = upsTabOrder;
            } else if ($('#non-ups-calc-wrap').is(':visible')) {
                currentTabOrder = nonUpsTabOrder;
            } else {
                return;
            }
            
            const currentIndex = currentTabOrder.indexOf(currentId);
            if (currentIndex === -1) return;
            
            // Find next element
            for (let i = currentIndex + 1; i < currentTabOrder.length; i++) {
                const nextSelector = currentTabOrder[i];
                const $next = $(nextSelector);
                if ($next.length && $next.is(':visible')) {
                    if ($next.is('button')) {
                        $next.click();
                    } else {
                        $next.focus();
                        if ($next.hasClass('select2-hidden-accessible')) {
                            $next.select2('open');
                        }
                    }
                    break;
                }
            }
        }, 100);
    });
}

function updateItemList() {
    const list = $("#item-list");
    list.empty();
    if (items.length === 0) {
        list.append('<span class="text-muted">No items added.</span>');
    } else {
        items.forEach((item, idx) => {
            list.append(`<div>${item.name} x ${item.quantity} (each ${item.weight} lbs, $${item.cost ? item.cost.toFixed(2) : '0.00'} per unit) <button class='btn btn-sm btn-danger ms-2' onclick='removeItem(${idx})'>Remove</button></div>`);
        });
    }
}

function removeItem(idx) {
    items.splice(idx, 1);
    updateItemList();
}

function loadItemNames() {
    $.ajax({
        url: "/api/item_names",
        method: "GET",
        success: function(data) {
            const select = $("#item_name");
            select.empty();
            data.items.forEach(name => {
                select.append(new Option(name, name));
            });
            select.val(null).trigger('change');
            // Also update remove dropdown
            const removeSelect = $("#remove_item_name");
            if (removeSelect.length) {
                removeSelect.empty();
                data.items.forEach(name => {
                    removeSelect.append(new Option(name, name));
                });
                removeSelect.val(null).trigger('change');
            }
        },
        error: function(xhr) {
            alert("Error loading item names: " + xhr.responseJSON.error);
        }
    });
}

let averagesData = [];
function renderAveragesTable(filterText = "") {
    const tbody = $("#averages-table-body");
    tbody.empty();
    let filtered = averagesData;
    if (filterText) {
        const search = filterText.toLowerCase();
        filtered = averagesData.filter(item =>
            (item.name && item.name.toLowerCase().includes(search)) ||
            (item.vendor && item.vendor.toLowerCase().includes(search))
        );
    }
    if (!filtered || filtered.length === 0) {
        tbody.append('<tr><td colspan="4" class="text-center text-muted">No results.</td></tr>');
        return;
    }
    filtered.forEach(item => {
        let upsCell = '';
        if ((item.UPS || '').toLowerCase() === 'yes') {
            upsCell = `<td style='background:#c8e6c9; color:#256029; font-weight:bold;'>Yes</td>`;
        } else if ((item.UPS || '').toLowerCase() === 'no') {
            upsCell = `<td style='background:#ffcdd2; color:#b71c1c; font-weight:bold;'>No</td>`;
        } else {
            upsCell = `<td>${item.UPS || ''}</td>`;
        }
        tbody.append(`<tr><td>${item.name}</td><td>${item.vendor || ''}</td><td class='shipping-cell' data-shipping='${item.avg_per_unit_shipping_offset}' data-item='${item.name}' data-vendor='${item.vendor || ''}'>$${item.avg_per_unit_shipping_offset.toFixed(2)}</td>${upsCell}</tr>`);
    });
    // Attach click handler for shipping cost cells
    $(".shipping-cell").css('cursor', 'pointer').attr('title', 'Click to calculate retail').off('click').on('click', function() {
        const shipping = parseFloat($(this).data('shipping'));
        const item = $(this).data('item');
        const vendor = $(this).data('vendor');
        let itemCost = prompt(`Enter item cost for '${item}'${vendor ? ' (Vendor: ' + vendor + ')' : ''}:`);
        if (itemCost === null) return;
        itemCost = parseFloat(itemCost);
        if (isNaN(itemCost) || itemCost < 0) {
            alert('Invalid item cost.');
            return;
        }
        // Only use offset shipping
        const retail50 = (itemCost + shipping) / 0.5;
        const retail55 = (itemCost + shipping) / 0.45;
        const retail60 = (itemCost + shipping) / 0.4;
        alert(`Suggested Retail Prices:\n50% margin: $${retail50.toFixed(2)}\n55% margin: $${retail55.toFixed(2)}\n60% margin: $${retail60.toFixed(2)}`);
    });
}
function loadAveragesPanel() {
    $.ajax({
        url: "/api/item_shipping_averages",
        method: "GET",
        success: function(data) {
            averagesData = data.items || [];
            renderAveragesTable($("#averages-search").val() || "");
        },
        error: function() {
            averagesData = [];
            renderAveragesTable($("#averages-search").val() || "");
        }
    });
}
$(document).on("input", "#averages-search", function() {
    renderAveragesTable($(this).val() || "");
});

$(document).ready(function() {
    // Setup keyboard navigation
    setupKeyboardNavigation();
    
    $("#item_name").select2({
        placeholder: "Select an item",
        allowClear: true,
        width: 'resolve'
    });
    loadItemNames();

    // Fetch vendor list from backend and populate dropdowns
    function loadVendorsDropdowns() {
        $.ajax({
            url: "/api/vendors",
            method: "GET",
            success: function(data) {
                const vendors = data.vendors || [];
                // UPS calculator dropdown
                const vendorZipSelect = $("#vendor_zip");
                vendorZipSelect.empty();
                vendorZipSelect.append(new Option("", "")); // allow blank
                vendors.forEach(opt => {
                    vendorZipSelect.append(new Option(`${opt.vendor} - ${opt.zip}`, opt.zip));
                });
                vendorZipSelect.select2({
                    placeholder: "Select or enter a ZIP code",
                    allowClear: true,
                    tags: true,
                    width: 'resolve',
                    createTag: function(params) {
                        // Only allow numeric zip codes as custom entries
                        var term = $.trim(params.term);
                        if (/^\d{5}$/.test(term)) {
                            return { id: term, text: term, newTag: true };
                        }
                        return null;
                    }
                });
                // Non-UPS calculator dropdown
                const nonUpsVendorSelect = $("#non_ups_vendor_zip");
                if (nonUpsVendorSelect.length) {
                    nonUpsVendorSelect.empty();
                    nonUpsVendorSelect.append(new Option("", ""));
                    vendors.forEach(opt => {
                        nonUpsVendorSelect.append(new Option(`${opt.vendor} - ${opt.zip}`, opt.zip));
                    });
                    nonUpsVendorSelect.select2({
                        placeholder: "Select or enter a ZIP code",
                        allowClear: true,
                        tags: true,
                        width: 'resolve',
                        createTag: function(params) {
                            var term = $.trim(params.term);
                            if (/^\d{5}$/.test(term)) {
                                return { id: term, text: term, newTag: true };
                            }
                            return null;
                        }
                    });
                }
            },
            error: function(xhr) {
                alert("Error loading vendors: " + (xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : "Unknown error"));
            }
        });
    }
    loadVendorsDropdowns();

    // Add item form handler
    $("#add-item-form").submit(function(e) {
        e.preventDefault();
        const name = $("#new_item_name").val().trim();
        const weight = parseFloat($("#new_item_weight").val());
        const msgBox = $("#add-item-msg");
        msgBox.text("");
        if (!name || isNaN(weight) || weight <= 0) {
            msgBox.text("Please enter a valid name and weight.").css("color", "#d32f2f");
            return;
        }
        $.ajax({
            url: "/api/add_item",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ name, weight }),
            success: function(data) {
                msgBox.text("Item added!").css("color", "#388e3c");
                $("#new_item_name").val("");
                $("#new_item_weight").val("");
                loadItemNames(); // reload dropdown
            },
            error: function(xhr) {
                let err = xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : "Error adding item.";
                msgBox.text(err).css("color", "#d32f2f");
            }
        });
    });

    // Remove item select2
    $("#remove_item_name").select2({
        placeholder: "Select an item",
        allowClear: true,
        width: 'resolve'
    });
    // Remove item form handler
    $("#remove-item-form").submit(function(e) {
        e.preventDefault();
        const name = $("#remove_item_name").val();
        const msgBox = $("#remove-item-msg");
        msgBox.text("");
        if (!name) {
            msgBox.text("Please select an item to remove.").css("color", "#d32f2f");
            return;
        }
        $.ajax({
            url: "/api/remove_item",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ name }),
            success: function(data) {
                msgBox.text("Item removed!").css("color", "#388e3c");
                loadItemNames(); // reload dropdowns
            },
            error: function(xhr) {
                let err = xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : "Error removing item.";
                msgBox.text(err).css("color", "#d32f2f");
            }
        });
    });

    // Robustly populate weight dropdown for Non-UPS calculator using a single API call
    function loadNonUpsWeightDropdown() {
        $.ajax({
            url: "/api/items_with_weights",
            method: "GET",
            success: function(data) {
                const select = $("#non_ups_item_weight");
                select.empty();
                select.append(new Option("", "")); // Add empty option
                const items = data.items || [];
                if (items.length === 0) return;
                items.sort((a, b) => a.name.localeCompare(b.name));
                items.forEach(item => {
                    const value = JSON.stringify({ name: item.name, weight: item.weight });
                    select.append(new Option(`${item.name} (${item.weight} lbs)`, value));
                });
                select.val("").trigger('change');
            }
        });
    }
    loadNonUpsWeightDropdown();

    // Make the Non-UPS item weight dropdown searchable and allow manual entry
    $("#non_ups_item_weight").select2({
      placeholder: "Select or enter item weight",
      allowClear: true,
      width: 'resolve',
      tags: true // allow manual entry
    });

    // Add Non UPS Item form handler
    $("#add-non-ups-item-form").submit(function(e) {
        e.preventDefault();
        const name = $("#non_ups_vendor_item").val().trim();
        const quantity = parseInt($("#non_ups_quantity").val());
        const freight = parseFloat($("#non_ups_freight").val());
        const msgBox = $("#add-non-ups-item-msg");
        msgBox.text("");
        if (!name || isNaN(quantity) || quantity <= 0 || isNaN(freight) || freight <= 0) {
            msgBox.text("Please enter valid values.").css("color", "#d32f2f");
            return;
        }
        $.ajax({
            url: "/api/add_non_ups_item",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ name, quantity, freight }),
            success: function(data) {
                msgBox.text("Non UPS item added!").css("color", "#388e3c");
                $("#non_ups_vendor_item").val("");
                $("#non_ups_quantity").val("");
                $("#non_ups_freight").val("");
                loadAveragesPanel();
            },
            error: function(xhr) {
                let err = xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : "Error adding non UPS item.";
                msgBox.text(err).css("color", "#d32f2f");
            }
        });
    });

    // Non UPS calculator logic
    let nonUpsItems = [];
    function updateNonUpsItemsList() {
      const list = $("#non-ups-items-list");
      list.empty();
      if (nonUpsItems.length === 0) {
        list.append('<span class="text-muted">No items added.</span>');
      } else {
        nonUpsItems.forEach((item, idx) => {
          list.append(`<div>${item.name} x ${item.quantity} (each ${item.weight ? item.weight + ' lbs' : 'N/A'}) <button class='btn btn-sm btn-danger ms-2' onclick='removeNonUpsItem(${idx})'>Remove</button></div>`);
        });
      }
    }
    window.removeNonUpsItem = function(idx) {
      nonUpsItems.splice(idx, 1);
      updateNonUpsItemsList();
    };
    $("#add-non-ups-item-btn").off('click').on('click', function() {
        const name = $("#non_ups_item_name").val().trim();
        const quantity = parseInt($("#non_ups_item_quantity").val());
        let weight = null;
        const weightData = $("#non_ups_item_weight").val();
        if (weightData) {
            try {
                // If it's a JSON string, parse it
                const parsed = JSON.parse(weightData);
                if (typeof parsed === 'object' && parsed.weight !== undefined) {
                    weight = parsed.weight;
                } else if (!isNaN(weightData)) {
                    weight = parseFloat(weightData);
                }
            } catch (e) {
                // If not JSON, try to parse as float
                if (!isNaN(weightData)) {
                    weight = parseFloat(weightData);
                }
            }
        }
        if (!name || isNaN(quantity) || quantity <= 0) {
            alert("Please enter a valid item name and quantity.");
            return;
        }
        nonUpsItems.push({ name, quantity, weight });
        updateNonUpsItemsList();
        $("#non_ups_item_name").val("");
        $("#non_ups_item_quantity").val("");
        $("#non_ups_item_weight").val("").trigger('change');
    });
    $("#clear-non-ups-items-btn").click(function() {
      nonUpsItems = [];
      updateNonUpsItemsList();
      $("#non-ups-result-box").hide();
    });
    $("#non-ups-calc-form").submit(function(e) {
      e.preventDefault();
      if (nonUpsItems.length === 0) {
        alert("Please add at least one item.");
        return;
      }
      const vendor_zip = $("#non_ups_vendor_zip").val();
      const vendor_label = $("#non_ups_vendor_zip option:selected").text();
      const freight = parseFloat($("#non_ups_freight_total").val());
      if (!vendor_zip || isNaN(freight) || freight <= 0) {
        alert("Please select a vendor and enter a valid freight cost.");
        return;
      }
      // If all weights are missing or zero, split by quantity
      const anyWeight = nonUpsItems.some(item => item.weight && !isNaN(item.weight) && item.weight > 0);
      let totalWeight = 0;
      if (anyWeight) {
        totalWeight = nonUpsItems.reduce((sum, item) => sum + ((item.weight && !isNaN(item.weight) && item.weight > 0 ? item.weight : 1) * item.quantity), 0);
      } else {
        totalWeight = nonUpsItems.reduce((sum, item) => sum + item.quantity, 0);
      }
      // Build the complete HTML output first
      let html = `<h5>Freight Split</h5>`;
      html += `<div>Total freight: <b>$${freight.toFixed(2)}</b></div>`;
      html += `<div>Total shipment weight: <b>${anyWeight ? totalWeight.toFixed(2) + ' lbs' : 'N/A'}</b></div>`;
      html += `<hr style='margin: 10px 0;'/>`;
      html += `<h6>Cost Breakdown by Item</h6>`;
      
      // Build HTML for all items first
      nonUpsItems.forEach(item => {
        let itemTotalWeight = 0;
        if (anyWeight) {
          itemTotalWeight = (item.weight && !isNaN(item.weight) && item.weight > 0 ? item.weight : 1) * item.quantity;
        } else {
          itemTotalWeight = item.quantity;
        }
        const share = totalWeight ? (itemTotalWeight / totalWeight) : 0;
        const itemFreight = share * freight;
        const perUnitFreight = itemFreight / item.quantity;
        html += `<div style='background: #f8f9fa; padding: 12px; border-radius: 6px; margin-bottom: 10px; border-left: 3px solid #13294B;'>`;
        html += `<div style='font-weight: bold; color: #13294B; font-size: 1.1em; margin-bottom: 8px;'>${item.name}</div>`;
        html += `<div>Quantity: <b>${item.quantity}</b> | Weight per unit: <b>${item.weight ? item.weight + ' lbs' : 'N/A'}</b></div>`;
        html += `<div>Freight per unit: <span style='font-weight: bold; color: #FF552E;'>$${perUnitFreight.toFixed(2)}</span></div>`;
        html += `</div>`;
      });
      
      // Show the results immediately
      $("#non-ups-result-box").html(html).show();
      
      // Now save each item to history SEQUENTIALLY to avoid race conditions
      let saveCount = 0;
      let saveErrors = [];
      
      // Function to save items one by one
      function saveItemSequentially(index) {
        if (index >= nonUpsItems.length) {
          // All items processed
          if (saveErrors.length > 0) {
            console.warn("Some items failed to save:", saveErrors);
            alert("Warning: " + saveErrors.length + " items failed to save to history. Check console for details.");
          }
          loadAveragesPanel();
          return;
        }
        
        const item = nonUpsItems[index];
        
        // Calculate the same values for this item (for saving to history)
        let itemTotalWeight = 0;
        if (anyWeight) {
          itemTotalWeight = (item.weight && !isNaN(item.weight) && item.weight > 0 ? item.weight : 1) * item.quantity;
        } else {
          itemTotalWeight = item.quantity;
        }
        const share = totalWeight ? (itemTotalWeight / totalWeight) : 0;
        const itemFreight = share * freight;
        
        // Extract vendor name safely
        let vendorName = '';
        if (vendor_label && vendor_label.includes(' - ')) {
          vendorName = vendor_label.split(' - ')[0].trim();
        } else if (vendor_label) {
          vendorName = vendor_label.trim();
        } else {
          vendorName = vendor_zip || 'Unknown';
        }
        
        // Validate data before sending
        if (!item.name || !item.name.trim()) {
          const errorMsg = `Invalid item name for item ${index + 1}`;
          saveErrors.push(errorMsg);
          console.error(errorMsg);
          saveItemSequentially(index + 1); // Move to next item
          return;
        }
        
        if (!item.quantity || item.quantity <= 0) {
          const errorMsg = `Invalid quantity for item ${item.name}`;
          saveErrors.push(errorMsg);
          console.error(errorMsg);
          saveItemSequentially(index + 1); // Move to next item
          return;
        }
        
        if (!itemFreight || itemFreight <= 0) {
          const errorMsg = `Invalid freight cost for item ${item.name}`;
          saveErrors.push(errorMsg);
          console.error(errorMsg);
          saveItemSequentially(index + 1); // Move to next item
          return;
        }
        
        // Ensure weight_used is a valid number or null
        let weightUsed = item.weight;
        if (weightUsed !== null && weightUsed !== undefined) {
          if (isNaN(weightUsed) || weightUsed < 0) {
            weightUsed = null;
          }
        }
        
        // Log the data being sent for debugging
        const saveData = { name: item.name.trim(), quantity: item.quantity, freight: itemFreight, vendor: vendorName, weight_used: weightUsed };
        console.log(`Saving item to history:`, saveData);
        
        // Save to history
        $.ajax({
          url: "/api/add_non_ups_item",
          method: "POST",
          contentType: "application/json",
          timeout: 10000, // 10 second timeout
          data: JSON.stringify(saveData),
          success: function(data) {
            console.log(`Successfully saved ${item.name} to history`);
            saveItemSequentially(index + 1); // Move to next item
          },
          error: function(xhr, status, error) {
            let errorMsg;
            if (status === 'timeout') {
              errorMsg = `Timeout saving ${item.name} to history`;
            } else if (xhr.responseJSON && xhr.responseJSON.error) {
              errorMsg = `Failed to save ${item.name}: ${xhr.responseJSON.error}`;
            } else {
              errorMsg = `Failed to save ${item.name}: ${error} (Status: ${status})`;
            }
            saveErrors.push(errorMsg);
            console.error(errorMsg);
            saveItemSequentially(index + 1); // Move to next item even on error
          }
        });
      }
      
      // Start the sequential saving process
      saveItemSequentially(0);
      $("#non-ups-result-box").html(html).show();
    });
    // Populate vendor dropdown for non UPS calc
    // This function is no longer needed as vendors are loaded dynamically
    // Show/hide logic for calculators
    $("#show-non-ups-calc").click(function() {
      $("#shipping-form, #result-box").hide();
      $("#non-ups-calc-wrap").show();
    });
    $("#close-non-ups-calc").click(function() {
      $("#non-ups-calc-wrap").hide();
      $("#shipping-form, #result-box").show();
    });

    // Add Vendor form handler (AJAX)
    $("#add-vendor-form").submit(function(e) {
        e.preventDefault();
        const name = $("#vendor_name_input").val().trim();
        const zip = $("#vendor_zip_input").val().trim();
        const msgBox = $("#add-vendor-msg");
        msgBox.text("");
        if (!name || !/^\d{5}$/.test(zip)) {
            msgBox.text("Please enter a valid vendor name and 5-digit ZIP code.").css("color", "#d32f2f");
            return;
        }
        $.ajax({
            url: "/api/add_vendor",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ name: name, zip: zip }),
            success: function(data) {
                msgBox.text("Vendor added!").css("color", "#388e3c");
                $("#vendor_name_input").val("");
                $("#vendor_zip_input").val("");
                loadVendorsDropdowns(); // reload dropdowns
            },
            error: function(xhr) {
                let err = xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : "Error adding vendor.";
                msgBox.text(err).css("color", "#d32f2f");
            }
        });
    });
});

$("#add-item-btn").click(function() {
    const name = $("#item_name").val();
    const quantity = parseInt($("#item_quantity").val());
    const cost = parseFloat($("#item_cost").val());
    const vendor_zip_val = $("#vendor_zip").val();
    const vendor_label = $("#vendor_zip option:selected").text();
    let vendor = '';
    if (vendor_label && vendor_label.includes(' - ')) {
        vendor = vendor_label.split(' - ')[0].trim();
    } else if (vendor_zip_val) {
        vendor = vendor_zip_val;
    }
    if (!name || isNaN(quantity) || quantity <= 0 || isNaN(cost) || cost < 0) {
        alert("Please select a valid item, quantity, and cost.");
        return;
    }
    $.ajax({
        url: "/api/item_weight",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ name }),
        success: function(data) {
            items.push({ name, quantity, weight: data.weight, cost, vendor });
            updateItemList();
            $("#item_name").val(null).trigger('change');
            $("#item_quantity").val("");
            $("#item_cost").val("");
        },
        error: function(xhr) {
            alert("Error getting weight for '" + name + "': " + xhr.responseJSON.error);
        }
    });
});

$("#clear-items-btn").click(function() {
    items = [];
    updateItemList();
    $("#result-box").hide();
});

$("#shipping-form").submit(function(e) {
    e.preventDefault();
    const vendor_zip = $("#vendor_zip").val() ? $("#vendor_zip").val().trim() : "";
    const vendor_label = $("#vendor_zip option:selected").text();
    const receiving_zip = $("#receiving_zip").val() ? $("#receiving_zip").val().trim() : "";
    const po_number = $("#po_number").val() ? $("#po_number").val().trim() : "";
    if (!vendor_zip || items.length === 0) {
        alert("Please enter a vendor ZIP and at least one item.");
        return;
    }
    $.ajax({
        url: "/api/calculate",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ vendor_zip, receiving_zip, items, vendor_label, po_number }),
        success: function(data) {
            let html = ``;
            html += `<div style="background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #FF552E; margin: 15px 0;">`;
            html += `<div style="font-size: 1.2em; font-weight: bold; color: #13294B; margin-bottom: 10px;">TOTAL SHIPPING COSTS:</div>`;
            html += `<div style="font-size: 1.1em; margin-bottom: 8px;">Estimated Total Shipping Cost: <span style="font-weight: bold; color: #FF552E; font-size: 1.2em;">$${data.offset_shipping_cost.toFixed(2)}</span></div>`;
            html += `<div style="font-size: 1.05em; margin-bottom: 4px;">Total Weight: <span style="font-weight: bold; color: #13294B;">${data.total_weight.toFixed(2)} lbs</span></div>`;
            html += `<div style="font-size: 1.05em; margin-bottom: 4px;">Zone: <span style="font-weight: bold; color: #13294B;">${data.zone}</span></div>`;
            html += `</div>`;
            html += `<hr style="border-top: 2px solid #13294B; margin: 15px 0;"/>`;
            html += `<h6 style="color: #13294B; font-weight: bold;">Cost Breakdown by Item</h6>`;
            data.items.forEach(item => {
                html += `<div style="background: #f8f9fa; padding: 12px; border-radius: 6px; margin-bottom: 10px; border-left: 3px solid #13294B;">`;
                html += `<div style="font-weight: bold; color: #13294B; font-size: 1.1em; margin-bottom: 8px;">${item.name}</div>`;
                html += `<div style="margin-bottom: 5px;">Quantity: <b>${item.quantity}</b> | Weight per unit: <b>${item.weight_per_unit} lbs</b> | Item cost: <b>$${item.cost ? item.cost.toFixed(2) : '0.00'}</b></div>`;
                html += `<div style="margin-bottom: 5px; display:none;">Estimated shipping per unit: <span style="font-weight: bold; color: #13294B;">$${item.offset_shipping_per_unit.toFixed(2)}</span></div>`;
                html += `<div>Estimated shipping per unit: <span style="font-weight: bold; color: #FF552E;">$${item.offset_shipping_per_unit.toFixed(2)}</span></div>`;
                html += `<div style='margin-top:8px;'><b>Suggested Retail (50% margin):</b> $${item.retail_50.toFixed(2)}<br/>`;
                html += `<b>Suggested Retail (55% margin):</b> $${item.retail_55.toFixed(2)}<br/>`;
                html += `<b>Suggested Retail (60% margin):</b> $${item.retail_60.toFixed(2)}</div>`;
                html += `</div>`;
            });
            $("#result-box").html(html).show();
            loadAveragesPanel(); // update averages after calculation
            // Clear items after calculation (robust)
            while (items.length > 0) { items.pop(); }
            updateItemList();
            // Clear item selection fields
            $("#item_name").val(null).trigger('change');
            $("#item_quantity").val("");
            $("#item_cost").val("");
        },
        error: function(xhr) {
            alert("Error calculating shipping: " + xhr.responseJSON.error);
        }
    });
});

// Initial render
updateItemList();
loadAveragesPanel();

function updateNonUpsItemNameField() {
    const vendor_zip = $("#non_ups_vendor_zip").val();
    const vendor_label = $("#non_ups_vendor_zip option:selected").text();
    const vendor = vendor_label ? vendor_label.split(' - ')[0].trim() : '';
    const container = $("#non_ups_item_name").parent();
    if (vendor) {
        // Replace with select2 dropdown
        if (!$("#non_ups_item_name").is('select')) {
            $("#non_ups_item_name").replaceWith('<select class="form-control" id="non_ups_item_name" style="width:100%"></select>');
        }
        const select = $("#non_ups_item_name");
        select.empty();
        $.ajax({
            url: "/api/item_names_by_vendor",
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({ vendor }),
            success: function(data) {
                data.items.forEach(name => {
                    select.append(new Option(name, name));
                });
                select.val(null).trigger('change');
            }
        });
        select.select2({
            placeholder: "Select item name",
            allowClear: true,
            width: 'resolve',
            tags: true // allow new entries
        });
    } else {
        // Replace with text input
        if (!$("#non_ups_item_name").is('input')) {
            $("#non_ups_item_name").replaceWith('<input type="text" class="form-control" id="non_ups_item_name">');
        }
    }
}
// Attach to vendor dropdown change
$(document).on('change', '#non_ups_vendor_zip', function() {
    updateNonUpsItemNameField();
});
// Also call on page load
updateNonUpsItemNameField();
$(document).on('change', '#non_ups_item_name', function() {
    const item = $(this).val();
    const vendor_label = $('#non_ups_vendor_zip option:selected').text();
    const vendor = vendor_label ? vendor_label.split(' - ')[0].trim() : '';
    if (item && vendor) {
        $.ajax({
            url: '/api/last_weight_used',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ item_name: item, vendor: vendor }),
            success: function(data) {
                if (data.weight !== null && data.weight !== undefined && data.weight !== '') {
                    // Find the option in the weight dropdown and select it if present, else add it
                    const select = $('#non_ups_item_weight');
                    let found = false;
                    select.find('option').each(function() {
                        if (parseFloat($(this).val()) === parseFloat(data.weight)) {
                            found = true;
                            select.val($(this).val()).trigger('change');
                        }
                    });
                    if (!found) {
                        // Add the option and select it
                        const label = `${item} (${data.weight} lbs)`;
                        const value = JSON.stringify({ name: item, weight: data.weight });
                        select.append(new Option(label, value));
                        select.val(value).trigger('change');
                    }
                }
            }
        });
    }
});
</script>
</body>
</html>
'''

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False) 