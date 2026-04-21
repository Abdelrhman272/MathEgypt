# -*- coding: utf-8 -*-
{
    "name": "Agricultural Export Management",
    "summary": (
        "Complete farm-to-shipment workflow for agricultural exporters: "
        "farm evaluations, production batches, lot reservation, cold storage, "
        "container management, logistics costing, season P&L, and profitability dashboard."
    ),
    "description": """
Agricultural Export Management
================================

A complete end-to-end Odoo 19 vertical solution for agricultural exporters,
packhouses, and fresh produce trading companies.

Key Features
------------
**Farm Evaluation**
- Pre-purchase yield assessment per grade and size
- Expected vs actual comparison with achievement %
- One-click Purchase Order creation with analytic distribution

**Production Batch**
- Raw material → finished goods with lot tracking
- Scrap/waste recording with 5 types and cost impact
- Packaging material costs integrated into absorption costing
- Optional Manufacturing Order (MRP) mode

**Export Shipment**
- Container management with lot-level reservation (FIFO, grade/size filtered)
- Lot → Carton → Pallet hierarchy
- Logistics cost allocation (5 allocation bases)
- Automated Sales Order and Delivery Order generation
- Business validations (ETD, ETA, duplicate B/L, UoM enforcement)

**Season Analytics**
- Automatic analytic account per season
- Full P&L per season via Odoo Analytic Reports
- Dashboard with 8 KPIs and 4 interactive charts

**Customer Claims**
- Claims linked to shipment, lots, batch, and farm
- Full lifecycle with credit note integration

**Multi-Company (Intercompany)**
- Suggestion system for linking intercompany SOs to evaluations
- No silent auto-linking — user always confirms

**Reports**
- Shipment Packing List (customer-facing, no internal costs)
- Certificate of Origin (customs-ready with lot/batch/farm traceability)
- Farm Evaluation Report
- Production Batch Report (inputs, outputs, costing, yield)

**Arabic Support**
- Full Arabic translation (i18n/ar.po, 180+ strings)

Standard Odoo Integration
--------------------------
- Farms: res.partner with "Is Agricultural Farm" flag
- Crops: product.category with "Is Agricultural Crop" flag
- Destination: res.country + port field
- QC Inspections: uses Odoo Quality module (optional)

Required Modules
----------------
base, mail, purchase, stock, mrp, sale_management,
account, analytic, base_setup, stock_landed_costs

Configuration
-------------
1. Set Raw/Production/Finished Goods/Cold Storage locations in Settings
2. Mark vendor partners as "Is Agricultural Farm"
3. Mark product categories as "Is Agricultural Crop"
4. Run Initialize Product Attributes wizard
5. Create and activate a Production Season
    """,
    "version": "19.0.9.0.0",
    "category": "Inventory/Inventory",
    "license": "OPL-1",
    "author": "NextGen Systems",
    "website": "https://www.nexgensystems.net",
    "support": "support@nexgensystems.net",
    "price": 100.0,
    "currency": "USD",
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
        "views/claim_views.xml",
        "report/agx_reports.xml",
    ],
    "test": [
        "tests/test_evaluation.py",
        "tests/test_batch.py",
        "tests/test_shipment.py",
    ],
    "assets": {
        "web.assets_backend": [
            "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js",
            "agri_export_management/static/src/css/dashboard.css",
            "agri_export_management/static/src/xml/dashboard.xml",
            "agri_export_management/static/src/js/dashboard.js",
        ],
    },
    "images": [
        "static/description/banner.png",
    ],
    "demo": [
        "demo/demo_master.xml",
        "demo/demo_operations.xml",
        "demo/demo_shipments.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
