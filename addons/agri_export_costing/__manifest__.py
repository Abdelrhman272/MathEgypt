# -*- coding: utf-8 -*-
{
    "name": "Agri Export Costing",
    "version": "19.0.3.0.0",
    "category": "Inventory",
    "summary": "Shipment logistics costing and MRP relative sales value cost sharing",
    "author": "NextGen Systems",
    "license": "LGPL-3",
    "depends": ["export_shipment", "agri_export_core", "sale_management", "mrp", "account", "mail", "stock_landed_costs"],
    "data": [
        "security/agri_export_costing_security.xml",
        "views/export_shipment_costing_views.xml",
        "views/agri_production_costing_views.xml",
        "views/menu.xml",
    ],
    "installable": True,
    "application": False,
}
