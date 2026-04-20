# -*- coding: utf-8 -*-
"""AGX demo data loader.

Run from Odoo shell:
    env.company._agx_load_demo_data()

What it creates
---------------
- AGX configuration on the current company
- Crop categories, grades, sizes, shipment cost types
- Farms, customers, products, packaging materials
- Draft + approved + full-flow evaluations
- Purchase order + validated incoming receipt with lots
- In-progress batch and done batch with generated output lots
- Cold storage transfer for the done batch
- Draft, reserved, and shipped shipments
- Resolved customer claim on the shipped shipment

The loader is intentionally idempotent at the company level. If the main
season code already exists, the function returns the existing records and
creates nothing else.
"""

from datetime import timedelta

from odoo import fields


DEMO_MAIN_SEASON_CODE = "DEMO-ORG-26"


def _qty_field(model):
    return "quantity" if "quantity" in model._fields else "qty_done"


def _product_type_key(model):
    return "detailed_type" if "detailed_type" in model._fields else "type"


def _get_or_create(model, domain, vals=None):
    rec = model.search(domain, limit=1)
    if rec:
        if vals:
            # Fill only empty / falsy fields to keep reruns safe.
            patch = {}
            for key, value in vals.items():
                if key in rec._fields and not rec[key] and value not in (False, None, ""):
                    patch[key] = value
            if patch:
                rec.write(patch)
        return rec
    return model.create(vals or {})


def _create_product(env, name, default_code, product_type, uom, categ, tracking="none",
                    purchase_ok=False, sale_ok=False, standard_price=0.0, list_price=0.0):
    tmpl_model = env["product.template"]
    type_key = _product_type_key(tmpl_model)
    vals = {
        "name": name,
        "default_code": default_code,
        type_key: product_type,
        "uom_id": uom.id,
        "uom_po_id": uom.id,
        "tracking": tracking,
        "purchase_ok": purchase_ok,
        "sale_ok": sale_ok,
        "categ_id": categ.id if categ else False,
        "standard_price": standard_price,
        "list_price": list_price,
    }
    tmpl = _get_or_create(tmpl_model, [("default_code", "=", default_code)], vals)
    return tmpl.product_variant_id


def _ensure_locations(company):
    env = company.env
    warehouse = env["stock.warehouse"].search([
        ("company_id", "=", company.id)
    ], limit=1)
    if not warehouse:
        raise ValueError("No stock warehouse found for company %s" % company.display_name)

    raw_loc = warehouse.lot_stock_id
    finished_loc = _get_or_create(
        env["stock.location"],
        [("company_id", "=", company.id), ("name", "=", "AGX Finished Goods")],
        {
            "name": "AGX Finished Goods",
            "usage": "internal",
            "location_id": raw_loc.id,
            "company_id": company.id,
        },
    )
    cold_loc = _get_or_create(
        env["stock.location"],
        [("company_id", "=", company.id), ("name", "=", "AGX Cold Storage")],
        {
            "name": "AGX Cold Storage",
            "usage": "internal",
            "location_id": raw_loc.id,
            "company_id": company.id,
        },
    )
    production_loc = _get_or_create(
        env["stock.location"],
        [("company_id", "=", company.id), ("name", "=", "AGX Production")],
        {
            "name": "AGX Production",
            "usage": "production",
            "location_id": raw_loc.id,
            "company_id": company.id,
        },
    )
    return warehouse, raw_loc, finished_loc, cold_loc, production_loc


def _prepare_company(company, service_product):
    env = company.env
    warehouse, raw_loc, finished_loc, cold_loc, production_loc = _ensure_locations(company)
    company.write({
        "agx_container_service_product_id": service_product.id,
        "agx_so_line_prefix": "Demo Shipment",
        "agx_default_logistics_basis": "carton",
        "agx_allow_vendor_bill_cost_source": True,
        "agx_allow_landed_cost_source": True,
        "agx_margin_precision": 2,
        "agx_use_mrp_production": False,
        "agx_auto_generate_lot_numbers": True,
        "agx_raw_material_location_id": raw_loc.id,
        "agx_production_location_id": production_loc.id,
        "agx_finished_goods_location_id": finished_loc.id,
        "agx_cold_storage_location_id": cold_loc.id,
        "agx_internal_picking_type_id": warehouse.int_type_id.id,
        "agx_outgoing_picking_type_id": warehouse.out_type_id.id,
    })
    return {
        "warehouse": warehouse,
        "raw_loc": raw_loc,
        "finished_loc": finished_loc,
        "cold_loc": cold_loc,
        "production_loc": production_loc,
    }


def _validate_picking(picking):
    env = picking.env
    qty_field = _qty_field(env["stock.move.line"])
    for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel") and m.product_uom_qty):
        if move.move_line_ids:
            for line in move.move_line_ids:
                if not getattr(line, qty_field, 0.0):
                    line[qty_field] = move.product_uom_qty
            continue
        env["stock.move.line"].create({
            "picking_id": picking.id,
            "move_id": move.id,
            "product_id": move.product_id.id,
            "product_uom_id": move.product_uom.id,
            "location_id": move.location_id.id,
            "location_dest_id": move.location_dest_id.id,
            qty_field: move.product_uom_qty,
        })
    res = picking.with_context(skip_immediate=True, skip_backorder=True).button_validate()
    if isinstance(res, dict):
        pending = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
        if pending:
            pending._action_done()
    return picking


def _create_receipt_from_po(po):
    picking = po.picking_ids.filtered(lambda p: p.picking_type_id.code == "incoming" and p.state not in ("done", "cancel"))[:1]
    if not picking:
        return po.env["stock.picking"]
    return _validate_picking(picking)


def _set_sale_order_amount(shipment, price_unit):
    if shipment.sale_order_id and shipment.sale_order_id.order_line:
        shipment.sale_order_id.order_line.write({"price_unit": price_unit})


def load_demo_data(env, company=None):
    company = company or env.company
    existing_main = env["agx.season"].search([
        ("company_id", "=", company.id),
        ("code", "=", DEMO_MAIN_SEASON_CODE),
    ], limit=1)
    if existing_main:
        return {
            "status": "exists",
            "company": company.display_name,
            "main_season": existing_main.name,
        }

    today = fields.Date.context_today(env.user)
    if isinstance(today, str):
        today = fields.Date.from_string(today)

    uom_kg = env.ref("uom.product_uom_kgm")
    uom_unit = env.ref("uom.product_uom_unit")
    country_eg = env.ref("base.eg", raise_if_not_found=False)
    country_nl = env.ref("base.nl", raise_if_not_found=False)
    country_sa = env.ref("base.sa", raise_if_not_found=False)

    # Crop categories
    orange_cat = _get_or_create(
        env["product.category"],
        [("name", "=", "Demo Valencia Orange"), ("company_id", "=", False)],
        {"name": "Demo Valencia Orange", "is_agx_crop": True},
    )
    grape_cat = _get_or_create(
        env["product.category"],
        [("name", "=", "Demo Grapes"), ("company_id", "=", False)],
        {"name": "Demo Grapes", "is_agx_crop": True},
    )
    services_cat = _get_or_create(
        env["product.category"],
        [("name", "=", "AGX Demo Services"), ("company_id", "=", False)],
        {"name": "AGX Demo Services"},
    )
    packaging_cat = _get_or_create(
        env["product.category"],
        [("name", "=", "AGX Demo Packaging"), ("company_id", "=", False)],
        {"name": "AGX Demo Packaging"},
    )

    # Master data
    grade_a = _get_or_create(env["agx.grade"], [("code", "=", "A")], {
        "name": "Grade A", "code": "A", "sequence": 10,
    })
    grade_b = _get_or_create(env["agx.grade"], [("code", "=", "B")], {
        "name": "Grade B", "code": "B", "sequence": 20,
    })
    grade_j = _get_or_create(env["agx.grade"], [("code", "=", "JUICE")], {
        "name": "Juice", "code": "JUICE", "sequence": 30,
    })

    size_40 = _get_or_create(env["agx.size"], [("number", "=", 40)], {
        "number": 40, "sequence": 40,
    })
    size_48 = _get_or_create(env["agx.size"], [("number", "=", 48)], {
        "number": 48, "sequence": 48,
    })
    size_56 = _get_or_create(env["agx.size"], [("number", "=", 56)], {
        "number": 56, "sequence": 56,
    })

    inland_cost = _get_or_create(env["agx.shipment.cost.type"], [("code", "=", "inland")], {
        "name": "Inland Transport", "code": "inland", "default_allocation_basis": "qty", "sequence": 10,
    })
    port_cost = _get_or_create(env["agx.shipment.cost.type"], [("code", "=", "port")], {
        "name": "Port Charges", "code": "port", "default_allocation_basis": "carton", "sequence": 20,
    })
    ocean_cost = _get_or_create(env["agx.shipment.cost.type"], [("code", "=", "ocean")], {
        "name": "Ocean Freight", "code": "ocean", "default_allocation_basis": "carton", "sequence": 30,
    })
    customs_cost = _get_or_create(env["agx.shipment.cost.type"], [("code", "=", "customs")], {
        "name": "Customs Clearance", "code": "customs", "default_allocation_basis": "equal", "sequence": 40,
    })

    # Partners
    farm_green = _get_or_create(env["res.partner"], [("name", "=", "Green Nile Farm")], {
        "name": "Green Nile Farm",
        "supplier_rank": 10,
        "is_agx_farm": True,
        "agx_farm_code": "FARM-GN-01",
        "agx_region": "Beheira",
        "country_id": country_eg.id if country_eg else False,
        "company_type": "company",
    })
    farm_delta = _get_or_create(env["res.partner"], [("name", "=", "Delta Sunrise Farm")], {
        "name": "Delta Sunrise Farm",
        "supplier_rank": 10,
        "is_agx_farm": True,
        "agx_farm_code": "FARM-DS-02",
        "agx_region": "Kafr El Sheikh",
        "country_id": country_eg.id if country_eg else False,
        "company_type": "company",
    })
    customer_rotterdam = _get_or_create(env["res.partner"], [("name", "=", "Rotterdam Fresh BV")], {
        "name": "Rotterdam Fresh BV",
        "customer_rank": 10,
        "country_id": country_nl.id if country_nl else False,
        "company_type": "company",
    })
    customer_riyadh = _get_or_create(env["res.partner"], [("name", "=", "Riyadh Fruit House")], {
        "name": "Riyadh Fruit House",
        "customer_rank": 10,
        "country_id": country_sa.id if country_sa else False,
        "company_type": "company",
    })

    # Products
    service_product = _create_product(
        env, "AGX Export Container Service", "AGX-SVC-CONT", "service", uom_unit,
        services_cat, tracking="none", sale_ok=True, purchase_ok=False, list_price=4800.0,
    )
    _prepare_company(company, service_product)

    raw_orange = _create_product(
        env, "Valencia Orange Raw", "AGX-RAW-ORG", "consu", uom_kg, orange_cat,
        tracking="lot", purchase_ok=True, sale_ok=False, standard_price=8.0,
    )
    packed_a40 = _create_product(
        env, "Valencia Orange / Grade A / Size 40", "AGX-FG-A40", "consu", uom_unit,
        orange_cat, tracking="lot", purchase_ok=False, sale_ok=True, standard_price=28.0, list_price=42.0,
    )
    packed_a48 = _create_product(
        env, "Valencia Orange / Grade A / Size 48", "AGX-FG-A48", "consu", uom_unit,
        orange_cat, tracking="lot", purchase_ok=False, sale_ok=True, standard_price=24.0, list_price=38.0,
    )
    packed_b56 = _create_product(
        env, "Valencia Orange / Grade B / Size 56", "AGX-FG-B56", "consu", uom_unit,
        orange_cat, tracking="lot", purchase_ok=False, sale_ok=True, standard_price=18.0, list_price=29.0,
    )

    # Packaging materials
    carton_15 = _get_or_create(env["agx.packaging.material"], [("code", "=", "CTN15")], {
        "name": "15kg Carton",
        "code": "CTN15",
        "material_type": "carton",
        "uom_id": uom_unit.id,
        "standard_unit_cost": 2.75,
    })
    pallet = _get_or_create(env["agx.packaging.material"], [("code", "=", "PLT")], {
        "name": "Wooden Pallet",
        "code": "PLT",
        "material_type": "pallet",
        "uom_id": uom_unit.id,
        "standard_unit_cost": 55.0,
    })
    sticker = _get_or_create(env["agx.packaging.material"], [("code", "=", "STK")], {
        "name": "Brand Sticker",
        "code": "STK",
        "material_type": "label",
        "uom_id": uom_unit.id,
        "standard_unit_cost": 0.12,
    })

    # Seasons
    season_main = env["agx.season"].create({
        "name": "Demo Orange Season 2026",
        "code": DEMO_MAIN_SEASON_CODE,
        "crop_category_id": orange_cat.id,
        "company_id": company.id,
        "date_start": today - timedelta(days=60),
        "date_end": today + timedelta(days=120),
        "state": "active",
        "note": "<p>Main season for the AGX demo dataset.</p>",
    })
    season_grape = env["agx.season"].create({
        "name": "Demo Grapes Season 2026",
        "code": "DEMO-GRP-26",
        "crop_category_id": grape_cat.id,
        "company_id": company.id,
        "date_start": today - timedelta(days=20),
        "date_end": today + timedelta(days=90),
        "state": "draft",
        "note": "<p>Secondary season used to show multi-season coverage.</p>",
    })

    # Evaluation 1: draft
    eval_draft = env["agx.evaluation"].create({
        "evaluation_date": today - timedelta(days=7),
        "company_id": company.id,
        "farm_partner_id": farm_delta.id,
        "crop_category_id": grape_cat.id,
        "season_id": season_grape.id,
        "farm_expected_qty": 3800.0,
        "line_ids": [
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_a.id,
                "size_id": size_40.id,
                "expected_ratio": 55.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 8.0,
                "note": "Draft planning scenario.",
            }),
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_b.id,
                "size_id": size_56.id,
                "expected_ratio": 45.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 6.5,
            }),
        ],
        "note": "<p>Draft evaluation kept intentionally open for demo users.</p>",
    })

    # Evaluation 2: approved only
    eval_approved = env["agx.evaluation"].create({
        "evaluation_date": today - timedelta(days=5),
        "company_id": company.id,
        "farm_partner_id": farm_delta.id,
        "crop_category_id": orange_cat.id,
        "season_id": season_main.id,
        "farm_expected_qty": 4200.0,
        "line_ids": [
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_a.id,
                "size_id": size_48.id,
                "expected_ratio": 62.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 8.6,
            }),
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_b.id,
                "size_id": size_56.id,
                "expected_ratio": 38.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 7.1,
            }),
        ],
        "note": "<p>Approved evaluation waiting for buyer confirmation.</p>",
    })
    eval_approved.action_approve()

    # Evaluation 3: full flow
    eval_full = env["agx.evaluation"].create({
        "evaluation_date": today - timedelta(days=3),
        "company_id": company.id,
        "farm_partner_id": farm_green.id,
        "crop_category_id": orange_cat.id,
        "season_id": season_main.id,
        "farm_expected_qty": 6000.0,
        "line_ids": [
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_a.id,
                "size_id": size_40.id,
                "expected_ratio": 50.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 9.2,
            }),
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_a.id,
                "size_id": size_48.id,
                "expected_ratio": 30.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 8.7,
            }),
            (0, 0, {
                "product_id": raw_orange.id,
                "grade_id": grade_b.id,
                "size_id": size_56.id,
                "expected_ratio": 20.0,
                "uom_id": uom_kg.id,
                "estimated_unit_price": 7.0,
            }),
        ],
        "note": "<p>Main evaluation used for PO, receipt, batch, shipment, and claim demo.</p>",
    })
    eval_full.action_approve()
    eval_full.action_create_purchase_order()

    po = eval_full.po_id
    po.button_confirm()
    receipt = _create_receipt_from_po(po)

    # Batch 1: in progress example
    batch_progress = env["agx.batch"].create({
        "batch_date": today - timedelta(days=1),
        "company_id": company.id,
        "season_id": season_main.id,
        "evaluation_id": eval_approved.id,
        "manual_raw_material_cost": 12000.0,
        "manual_operation_cost": 3500.0,
        "manual_other_cost": 1400.0,
        "packaging_line_ids": [
            (0, 0, {"material_id": carton_15.id, "qty": 120, "unit_cost": 2.75}),
            (0, 0, {"material_id": sticker.id, "qty": 120, "unit_cost": 0.12}),
        ],
        "scrap_line_ids": [
            (0, 0, {
                "scrap_type": "quality_reject",
                "scrap_qty": 85.0,
                "uom_id": uom_kg.id,
                "reason": "Sorting area rejects during inspection",
            }),
        ],
        "output_line_ids": [
            (0, 0, {
                "product_id": packed_a48.id,
                "grade_id": grade_a.id,
                "size_id": size_48.id,
                "qty": 120.0,
                "uom_id": uom_unit.id,
                "sales_price_unit": 39.0,
            }),
        ],
    })
    batch_progress.action_start()

    # Batch 2: full batch from the validated receipt
    batch_done = env["agx.batch"].create({
        "batch_date": today,
        "company_id": company.id,
        "season_id": season_main.id,
        "evaluation_id": eval_full.id,
        "manual_operation_cost": 5400.0,
        "manual_other_cost": 2100.0,
        "packaging_line_ids": [
            (0, 0, {"material_id": carton_15.id, "qty": 520, "unit_cost": 2.75}),
            (0, 0, {"material_id": pallet.id, "qty": 24, "unit_cost": 55.0}),
            (0, 0, {"material_id": sticker.id, "qty": 520, "unit_cost": 0.12}),
        ],
        "scrap_line_ids": [
            (0, 0, {
                "scrap_type": "natural_loss",
                "scrap_qty": 140.0,
                "uom_id": uom_kg.id,
            }),
            (0, 0, {
                "scrap_type": "damage",
                "scrap_qty": 35.0,
                "uom_id": uom_kg.id,
                "reason": "Minor packing line handling damage",
            }),
        ],
        "output_line_ids": [
            (0, 0, {
                "product_id": packed_a40.id,
                "grade_id": grade_a.id,
                "size_id": size_40.id,
                "qty": 240.0,
                "uom_id": uom_unit.id,
                "sales_price_unit": 43.0,
            }),
            (0, 0, {
                "product_id": packed_a48.id,
                "grade_id": grade_a.id,
                "size_id": size_48.id,
                "qty": 180.0,
                "uom_id": uom_unit.id,
                "sales_price_unit": 39.0,
            }),
            (0, 0, {
                "product_id": packed_b56.id,
                "grade_id": grade_b.id,
                "size_id": size_56.id,
                "qty": 100.0,
                "uom_id": uom_unit.id,
                "sales_price_unit": 30.0,
            }),
        ],
    })
    batch_done.action_start()
    batch_done.action_done()
    batch_done.action_move_to_cold_storage()

    # Shipment 1: draft only
    shipment_draft = env["agx.shipment"].create({
        "shipment_date": today + timedelta(days=4),
        "company_id": company.id,
        "customer_id": customer_riyadh.id,
        "destination_country_id": country_sa.id if country_sa else False,
        "destination_port": "Jeddah",
        "season_id": season_main.id,
        "container_count": 1,
        "container_no": "MSCU-DEM-1001",
        "seal_no": "SEAL-1001",
        "vessel_name": "Demo Horizon",
        "voyage_no": "DH-26-01",
        "etd": today + timedelta(days=5),
        "eta": today + timedelta(days=12),
        "line_ids": [
            (0, 0, {
                "product_id": packed_b56.id,
                "grade_id": grade_b.id,
                "size_id": size_56.id,
                "product_qty": 40.0,
                "uom_id": uom_unit.id,
                "carton_qty": 40.0,
                "net_weight": 600.0,
                "gross_weight": 680.0,
            }),
        ],
        "cost_line_ids": [
            (0, 0, {
                "name": "Draft Inland Cost",
                "cost_type_id": inland_cost.id,
                "allocation_basis": "qty",
                "manual_amount": 1200.0,
            }),
        ],
        "note": "<p>Draft shipment kept intentionally before reservation.</p>",
    })
    shipment_draft.action_create_sale_order()
    _set_sale_order_amount(shipment_draft, 3600.0)

    # Shipment 2: reserved
    shipment_reserved = env["agx.shipment"].create({
        "shipment_date": today + timedelta(days=6),
        "company_id": company.id,
        "customer_id": customer_rotterdam.id,
        "destination_country_id": country_nl.id if country_nl else False,
        "destination_port": "Rotterdam",
        "season_id": season_main.id,
        "container_count": 1,
        "container_no": "TEMU-DEM-2002",
        "seal_no": "SEAL-2002",
        "vessel_name": "North Star",
        "voyage_no": "NS-2602",
        "etd": today + timedelta(days=7),
        "eta": today + timedelta(days=19),
        "bl_number": "BL-DEMO-RSV-01",
        "line_ids": [
            (0, 0, {
                "product_id": packed_a48.id,
                "grade_id": grade_a.id,
                "size_id": size_48.id,
                "product_qty": 90.0,
                "uom_id": uom_unit.id,
                "carton_qty": 90.0,
                "net_weight": 1350.0,
                "gross_weight": 1470.0,
            }),
            (0, 0, {
                "product_id": packed_b56.id,
                "grade_id": grade_b.id,
                "size_id": size_56.id,
                "product_qty": 30.0,
                "uom_id": uom_unit.id,
                "carton_qty": 30.0,
                "net_weight": 450.0,
                "gross_weight": 505.0,
            }),
        ],
        "cost_line_ids": [
            (0, 0, {
                "name": "Inland Transport",
                "cost_type_id": inland_cost.id,
                "allocation_basis": "qty",
                "manual_amount": 1800.0,
            }),
            (0, 0, {
                "name": "Port Handling",
                "cost_type_id": port_cost.id,
                "allocation_basis": "carton",
                "manual_amount": 950.0,
            }),
            (0, 0, {
                "name": "Ocean Freight",
                "cost_type_id": ocean_cost.id,
                "allocation_basis": "carton",
                "manual_amount": 2600.0,
            }),
        ],
    })
    shipment_reserved.action_create_sale_order()
    _set_sale_order_amount(shipment_reserved, 6400.0)
    shipment_reserved.action_reserve()

    # Shipment 3: shipped + claim
    shipment_shipped = env["agx.shipment"].create({
        "shipment_date": today + timedelta(days=8),
        "company_id": company.id,
        "customer_id": customer_rotterdam.id,
        "destination_country_id": country_nl.id if country_nl else False,
        "destination_port": "Rotterdam",
        "season_id": season_main.id,
        "container_count": 2,
        "container_no": "OOLU-DEM-3003",
        "seal_no": "SEAL-3003",
        "vessel_name": "Baltic Pearl",
        "voyage_no": "BP-2603",
        "etd": today + timedelta(days=9),
        "eta": today + timedelta(days=21),
        "bl_number": "BL-DEMO-SHP-01",
        "line_ids": [
            (0, 0, {
                "product_id": packed_a40.id,
                "grade_id": grade_a.id,
                "size_id": size_40.id,
                "product_qty": 120.0,
                "uom_id": uom_unit.id,
                "carton_qty": 120.0,
                "net_weight": 1800.0,
                "gross_weight": 1950.0,
            }),
            (0, 0, {
                "product_id": packed_a48.id,
                "grade_id": grade_a.id,
                "size_id": size_48.id,
                "product_qty": 70.0,
                "uom_id": uom_unit.id,
                "carton_qty": 70.0,
                "net_weight": 1050.0,
                "gross_weight": 1150.0,
            }),
        ],
        "cost_line_ids": [
            (0, 0, {
                "name": "Inland Transport",
                "cost_type_id": inland_cost.id,
                "allocation_basis": "qty",
                "manual_amount": 2100.0,
            }),
            (0, 0, {
                "name": "Port Charges",
                "cost_type_id": port_cost.id,
                "allocation_basis": "carton",
                "manual_amount": 1200.0,
            }),
            (0, 0, {
                "name": "Ocean Freight",
                "cost_type_id": ocean_cost.id,
                "allocation_basis": "carton",
                "manual_amount": 3900.0,
            }),
            (0, 0, {
                "name": "Customs Clearance",
                "cost_type_id": customs_cost.id,
                "allocation_basis": "equal",
                "manual_amount": 1450.0,
            }),
        ],
    })
    # Put second line on second container for a more realistic split.
    if len(shipment_shipped.container_ids) > 1 and len(shipment_shipped.line_ids) > 1:
        shipment_shipped.line_ids[1].container_id = shipment_shipped.container_ids.sorted("sequence")[1].id
    shipment_shipped.action_create_sale_order()
    _set_sale_order_amount(shipment_shipped, 7100.0)
    shipment_shipped.action_reserve()

    # Enrich reserved lot lines for pallet/carton reporting.
    for idx, lot_line in enumerate(shipment_shipped.lot_line_ids.sorted("id"), start=1):
        lot_line.write({
            "carton_count": int(lot_line.reserved_qty),
            "pallet_ref": "PLT-%03d" % idx,
            "pallet_count": 1,
            "note": "Demo palletized reservation",
        })
    shipment_shipped.action_ship()

    claim = env["agx.claim"].create({
        "company_id": company.id,
        "shipment_id": shipment_shipped.id,
        "claim_type": "quality",
        "claimed_qty": 12.0,
        "claimed_value": 780.0,
        "agreed_credit": 450.0,
        "root_cause": "Minor colour variation observed after arrival.",
        "resolution": "Commercial credit agreed on the next invoice.",
        "note": "<p>Sample customer claim linked to shipped lots.</p>",
    })
    claim.action_review()
    claim.action_resolve()

    return {
        "status": "created",
        "company": company.display_name,
        "seasons": [season_main.name, season_grape.name],
        "evaluations": [eval_draft.name, eval_approved.name, eval_full.name],
        "purchase_order": po.name,
        "receipt": receipt.name if receipt else False,
        "batches": [batch_progress.name, batch_done.name],
        "shipments": [shipment_draft.name, shipment_reserved.name, shipment_shipped.name],
        "claim": claim.name,
    }
