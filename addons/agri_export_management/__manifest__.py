# -*- coding: utf-8 -*-
{
    "name": "Agricultural Export Management",
    "summary": (
        "End-to-end agricultural export operations: farm evaluations, "
        "production batches, lot-level inventory, cold storage, "
        "shipment management, logistics costing, season analytics, "
        "and profitability dashboards."
    ),
    "description": """
Agricultural Export Management
================================

A complete Odoo 19 module for agricultural export companies.

**Core Workflow**

Farm Evaluation → Purchase Order → Goods Receipt →
Production Batch → (Cold Storage) → Export Shipment →
Sales Order → Delivery

**Key Features**

* **Farm Evaluations** — pre-purchase yield assessment per grade/size
* **Production Batches** — raw-to-finished goods with automatic stock moves
* **Cold Storage** — optional refrigerated storage step between packing and export
* **Export Shipments** — container management, lot reservation, delivery creation
* **Logistics Costing** — multi-source cost lines with flexible allocation bases
* **Season Analytics** — analytic account per season for full P&L reporting
* **Product Variant Bridge** — Grade + Size = stock variant via Initialize wizard
* **Traceability** — lot → batch → farm → shipment in one click
* **Profitability Dashboard** — KPIs per period or per season

**Stock Location Map**

| Location | Purpose |
|---|---|
| Raw Material | Incoming produce from farms |
| Production | Virtual (usage=production); auto-zeros |
| Finished Goods | Packed cartons after batch |
| Cold Storage | Refrigerated storage before export |
| Customer | Virtual destination for shipped goods |

**Configuration**

After installation, go to Agricultural Export → Configuration → Settings
and configure the four stock locations and two picking types.

Run Configuration → Initialize Product Attributes to link Grade/Size
to product variants automatically.
    """,
    "version": "19.0.7.0.0",
    "category": "Inventory/Inventory",
    "author": "NextGen Systems",
    "website": "",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "purchase",
        "stock",
        "mrp",
        "sale_management",
        "account",
        "analytic",
        "base_setup",
        "stock_landed_costs",
    ],
    "data": [
        "security/agx_security.xml",
        "security/ir.model.access.csv",
        "data/agx_sequence.xml",
        "views/menu.xml",
        "views/dashboard_views.xml",
        "views/master_views.xml",
        "views/evaluation_views.xml",
        "views/batch_views.xml",
        "views/shipment_views.xml",
        "views/purchase_views.xml",
        "wizard/wizard_views.xml",
        "views/settings_views.xml",
        "views/qc_views.xml",
        "report/agx_reports.xml",
    ],
    "test": [
        "tests/test_evaluation.py",
        "tests/test_batch.py",
        "tests/test_shipment.py",
    ],
    "assets": {
        "web.assets_backend": [
            "agri_export_management/static/src/css/dashboard.css",
            "agri_export_management/static/src/xml/dashboard.xml",
            "agri_export_management/static/src/js/dashboard.js",
        ],
    },
    "application": True,
    "installable": True,
    "auto_install": False,
    "images": ["static/description/banner.png"],
}
