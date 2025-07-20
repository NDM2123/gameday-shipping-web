import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from static_data import get_zone_from_vendor_zip, get_shipping_cost
from google_sheets import get_item_names, get_item_weight, add_item_to_sheet, remove_item_from_sheet, save_shipping_history, get_shipping_history, delete_item_shipping_history
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
    receiving_zip = data.get("receiving_zip", "")
    items = data.get("items", [])
    try:
        zone = get_zone_from_vendor_zip(vendor_zip, receiving_zip)
        total_weight = sum(float(item.get("weight") or 0.0) * float(item.get("quantity") or 0.0) for item in items)
        total_shipping_cost = float(get_shipping_cost(zone, total_weight) or 0.0)
        offset_shipping_cost = float(total_shipping_cost * (1 + OFFSET_PERCENT))
        item_total = sum(float(item.get("weight") or 0.0) * float(item.get("quantity") or 0.0) for item in items)
        result = {
            "total_weight": total_weight,
            "zone": zone,
            "estimated_total_shipping_cost": total_shipping_cost,
            "offset_shipping_cost": offset_shipping_cost,
            "items": []
        }
        for item in items:
            weight = float(item.get("weight") or 0.0)
            quantity = float(item.get("quantity") or 0.0)
            safe_total_weight = float(total_weight or 0.0)
            weight_share = (weight * quantity / safe_total_weight) if safe_total_weight else 0.0
            est_item_cost = weight_share * total_shipping_cost
            est_cost_per_unit = est_item_cost / quantity if quantity else 0.0
            offset_item_cost = weight_share * offset_shipping_cost
            offset_cost_per_unit = offset_item_cost / quantity if quantity else 0.0
            # Append to history
            save_shipping_history(item["name"], est_cost_per_unit, offset_cost_per_unit)
            item_result = {
                "name": item["name"],
                "quantity": int(quantity),
                "weight_per_unit": weight,
                "estimated_shipping_per_unit": est_cost_per_unit,
                "offset_shipping_per_unit": offset_cost_per_unit
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
        
        # Group by item name and calculate averages
        item_averages = {}
        for record in history:
            item_name = record['Item Name']
            if item_name not in item_averages:
                item_averages[item_name] = {
                    'costs': [],
                    'offset_costs': []
                }
            item_averages[item_name]['costs'].append(record['Per-Unit Shipping Cost'])
            item_averages[item_name]['offset_costs'].append(record['Per-Unit Shipping Cost (Offset)'])
        
        # Calculate averages
        items = []
        for item_name, data in item_averages.items():
            avg_cost = sum(data['costs']) / len(data['costs'])
            avg_offset_cost = sum(data['offset_costs']) / len(data['offset_costs'])
            items.append({
                "name": item_name,
                "avg_per_unit_shipping": avg_cost,
                "avg_per_unit_shipping_offset": avg_offset_cost
            })
        
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/delete_item_shipping_history", methods=["POST"])
def api_delete_item_shipping_history():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400
    try:
        success = delete_item_shipping_history(name)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/all_gsf_classic_black_punched_out_400x.webp')
def serve_logo():
    return send_from_directory('assets', 'all_gsf_classic_black_punched_out_400x.webp')

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
            margin-top: 110px;
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
    <div class="container shadow p-4 bg-white rounded">
    <div class="uiuc-header">
        <img src="/all_gsf_classic_black_punched_out_400x.webp" alt="Logo" class="uiuc-logo">
        <h2 class="uiuc-title">Gameday Shipping Cost Estimator</h2>
    </div>
    <form id="shipping-form">
        <div class="row mb-3">
            <div class="col">
                <label for="vendor_zip" class="form-label">Vendor ZIP</label>
                <input type="text" class="form-control" id="vendor_zip" required>
            </div>
            <div class="col">
                <label for="receiving_zip" class="form-label">Receiving ZIP</label>
                <select class="form-control" id="receiving_zip" required></select>
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
            <div class="col-3 d-flex align-items-end">
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
</div>
    <!-- New right-side averages panel -->
    <div id="averages-panel" style="position: fixed; top: 40px; right: 50px; bottom: 50px; width: 320px; background: #f4f6fa; border: 1px solid #dee2e6; border-radius: 8px; box-shadow: 0 2px 8px rgba(19,41,75,0.08); padding: 18px 14px 14px 14px; z-index: 4; display: flex; flex-direction: column;">
      <div style="color:#13294B; font-weight:700; font-size:1.1em; margin-bottom:10px;">Avg. Per-Unit Shipping Costs</div>
      <input type="text" id="averages-search" class="form-control form-control-sm mb-2" placeholder="Search item..." style="font-size:0.97em;">
      <div style="flex: 1 1 auto; min-height: 0; overflow-y: auto;">
        <table class="table table-sm table-bordered bg-white" style="font-size:0.97em; margin-bottom:0;">
          <thead>
            <tr style="background:#e3e8f0;">
              <th>Item</th>
              <th>Avg. Per-Unit</th>
              <th>Avg. Offset</th>
              <th>Delete</th>
            </tr>
          </thead>
          <tbody id="averages-table-body">
            <tr><td colspan="4" class="text-center text-muted">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js"></script>
<script>
let items = [];

function updateItemList() {
    const list = $("#item-list");
    list.empty();
    if (items.length === 0) {
        list.append('<span class="text-muted">No items added.</span>');
    } else {
        items.forEach((item, idx) => {
            list.append(`<div>${item.name} x ${item.quantity} (each ${item.weight} lbs) <button class='btn btn-sm btn-danger ms-2' onclick='removeItem(${idx})'>Remove</button></div>`);
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
        filtered = averagesData.filter(item => item.name.toLowerCase().includes(search));
    }
    if (!filtered || filtered.length === 0) {
        tbody.append('<tr><td colspan="4" class="text-center text-muted">No results.</td></tr>');
        return;
    }
    filtered.forEach(item => {
        tbody.append(`<tr><td>${item.name}</td><td>$${item.avg_per_unit_shipping.toFixed(2)}</td><td>$${item.avg_per_unit_shipping_offset.toFixed(2)}</td><td><button class='btn btn-sm btn-danger delete-history-btn' data-name="${item.name}">Delete</button></td></tr>`);
    });
    // Attach click handler for delete buttons
    $(".delete-history-btn").click(function() {
        const name = $(this).data("name");
        if (confirm(`Delete all shipping history for '${name}'?`)) {
            $.ajax({
                url: "/api/delete_item_shipping_history",
                method: "POST",
                contentType: "application/json",
                data: JSON.stringify({ name }),
                success: function() { loadAveragesPanel(); },
                error: function(xhr) {
                    alert("Error deleting history: " + (xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : "Unknown error"));
                }
            });
        }
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
    $("#item_name").select2({
        placeholder: "Select an item",
        allowClear: true,
        width: 'resolve'
    });
    loadItemNames();

    // Populate Receiving ZIP dropdown
    const receivingSelect = $("#receiving_zip");
    const RECEIVING_ZIP_OPTIONS = [
        { name: "Illinois", zip: 61801 },
        { name: "Ball State", zip: 47303 },
        { name: "Indiana", zip: 47401 },
        { name: "Northwestern", zip: 60202 },
        { name: "Ohio State", zip: 45775 },
    ];
    RECEIVING_ZIP_OPTIONS.forEach(opt => {
        receivingSelect.append(new Option(opt.name, opt.zip));
    });
    $("#receiving_zip").select2({
        placeholder: "Select a location",
        allowClear: true,
        width: 'resolve'
    });

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
});

$("#add-item-btn").click(function() {
    const name = $("#item_name").val();
    const quantity = parseInt($("#item_quantity").val());
    if (!name || isNaN(quantity) || quantity <= 0) {
        alert("Please select a valid item and quantity.");
        return;
    }
    $.ajax({
        url: "/api/item_weight",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ name }),
        success: function(data) {
            items.push({ name, quantity, weight: data.weight });
            updateItemList();
            $("#item_name").val(null).trigger('change');
            $("#item_quantity").val("");
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
    const vendor_zip = $("#vendor_zip").val().trim();
    const receiving_zip = $("#receiving_zip").val();
    if (!vendor_zip || !receiving_zip || items.length === 0) {
        alert("Please enter ZIP codes and at least one item.");
        return;
    }
    $.ajax({
        url: "/api/calculate",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ vendor_zip, receiving_zip, items }),
        success: function(data) {
            let html = `<h5>Shipping Summary</h5>`;
            html += `<div>Total shipment weight: <b>${data.total_weight.toFixed(2)} lbs</b></div>`;
            html += `<div>Shipping zone: <b>${data.zone}</b></div>`;
            html += `<div>Estimated total shipping cost: <b>$${data.estimated_total_shipping_cost.toFixed(2)}</b></div>`;
            html += `<div>Estimated total shipping cost (with 14% offset): <b>$${data.offset_shipping_cost.toFixed(2)}</b></div>`;
            html += `<hr/><h6>Cost Breakdown by Item</h6>`;
            data.items.forEach(item => {
                html += `<div><b>${item.name}</b> — Quantity: ${item.quantity}, Weight per unit: ${item.weight_per_unit} lbs<br/>Estimated shipping per unit: $${item.estimated_shipping_per_unit.toFixed(2)}<br/>Estimated shipping per unit (with 14% offset): $${item.offset_shipping_per_unit.toFixed(2)}</div><hr/>`;
            });
            $("#result-box").html(html).show();
            loadAveragesPanel(); // update averages after calculation
        },
        error: function(xhr) {
            alert("Error calculating shipping: " + xhr.responseJSON.error);
        }
    });
});

// Initial render
updateItemList();
loadAveragesPanel();
</script>
</body>
</html>
'''

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False) 