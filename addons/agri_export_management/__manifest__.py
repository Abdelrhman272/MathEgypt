# -*- coding: utf-8 -*-
{
    "name": "Agricultural Export",
    "summary": "Agricultural export operations, costing, and profitability",
    "version": "19.0.2.0.0",
    "category": "Inventory/Inventory",
    "author": "NextGen Systems",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "purchase",
        "stock",
        "mrp",
        "sale_management",
        "account",
        "base_setup",
        "stock_landed_costs"
    ],
    "data": [
        "security/agx_security.xml",
        "security/ir.model.access.csv",
        "data/agx_sequence.xml",
        "views/menu.xml",
        "views/master_views.xml",
        "views/evaluation_views.xml",
        "views/batch_views.xml",
        "views/shipment_views.xml",
        "views/settings_views.xml"
    ],
    "application": True,
    "installable": True,
}
