# -*- coding: utf-8 -*-
"""
test_full_flow.py — End-to-End Integration Tests

Tests the complete AGX workflow in one transaction:
  Farm Evaluation → PO → Receipt → Batch → Shipment

Each test class covers one realistic business scenario.
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
from odoo import fields


class TestFullWorkflow(TransactionCase):
    """Complete flow: Evaluation → PO → Batch → Shipment."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # ── Master data ──────────────────────────────────────────
        cls.crop    = env['product.category'].create({'name': 'Flow Orange', 'is_agx_crop': True})
        cls.grade_a = env['agx.grade'].create({'name': 'A', 'code': 'FA', 'sequence': 1})
        cls.grade_b = env['agx.grade'].create({'name': 'B', 'code': 'FB', 'sequence': 2})
        cls.size_40 = env['agx.size'].create({'number': 40, 'name': '40', 'sequence': 40})

        cls.vendor  = env['res.partner'].create({
            'name': 'Flow Farm Vendor', 'supplier_rank': 1, 'is_agx_farm': True,
            'country_id': env.ref('base.eg').id,
        })
        cls.customer = env['res.partner'].create({
            'name': 'Flow Customer NL', 'customer_rank': 1,
            'country_id': env.ref('base.nl').id,
        })
        cls.farm = env['res.partner'].create({
            'name': 'Flow Farm', 'code': 'FF-001', 'partner_id': cls.vendor.id
        })
        cls.season = env['agx.season'].create({
            'name': 'Flow Season 2025', 'code': 'FS-25',
            'crop_category_id': cls.crop.id, 'state': 'active',
        })
        cls.destination = env['res.country'].search([('code', '=', 'NL')], limit=1)

        # ── Products ─────────────────────────────────────────────
        uom_kg   = env.ref('uom.product_uom_kgm')
        uom_unit = env.ref('uom.product_uom_unit')

        cls.raw_product = env['product.template'].create({
            'name': 'Flow Raw Orange', 'type': 'consu',
            'tracking': 'lot', 'purchase_ok': True, 'sale_ok': False,
            'uom_id': uom_kg.id, 'uom_po_id': uom_kg.id,
        }).product_variant_id

        cls.fin_product = env['product.template'].create({
            'name': 'Flow Packed Orange', 'type': 'consu',
            'tracking': 'lot', 'sale_ok': True,
            'uom_id': uom_unit.id, 'uom_po_id': uom_unit.id,
        }).product_variant_id

        cls.svc_product = env['product.template'].create({
            'name': 'Flow Container Service', 'type': 'service',
            'sale_ok': True, 'uom_id': uom_unit.id,
        }).product_variant_id

        # ── Company settings ──────────────────────────────────────
        company = env.company
        company.agx_container_service_product_id = cls.svc_product.id

    def test_01_evaluation_lifecycle(self):
        """Full evaluation state machine."""
        ev = self.env['agx.evaluation'].create({
            'farm_partner_id': self.vendor.id,
            'partner_id': self.vendor.id,
            'crop_category_id': self.crop.id,
            'season_id': self.season.id,
            'evaluation_date': '2025-11-01',
            'farm_expected_qty': 5000.0,
            'line_ids': [
                (0, 0, {
                    'product_id': self.raw_product.id,
                    'grade_id': self.grade_a.id,
                    'expected_ratio': 60.0,
                    'estimated_unit_price': 3.0,
                }),
                (0, 0, {
                    'product_id': self.raw_product.id,
                    'grade_id': self.grade_b.id,
                    'expected_ratio': 40.0,
                    'estimated_unit_price': 2.0,
                }),
            ],
        })
        self.assertEqual(ev.state, 'draft')

        # Expected qty calculation
        line_a = ev.line_ids.filtered(lambda l: l.grade_id == self.grade_a)
        self.assertAlmostEqual(line_a.expected_qty, 3000.0, places=0)

        # Approve
        ev.action_approve()
        self.assertEqual(ev.state, 'approved')

        # Create PO
        ev.action_create_purchase_order()
        self.assertTrue(ev.po_id)
        self.assertEqual(ev.state, 'po_created')

        # PO has correct analytic distribution if season has analytic account
        if ev.season_id.analytic_account_id:
            for line in ev.po_id.order_line:
                self.assertIn(
                    str(ev.season_id.analytic_account_id.id),
                    (line.analytic_distribution or {}),
                )

    def test_02_batch_with_scrap_and_packaging(self):
        """Batch costing includes scrap + packaging."""
        batch = self.env['agx.batch'].create({
            'batch_date': '2025-12-01',
            'season_id': self.season.id,
        })
        batch.action_start()
        self.assertEqual(batch.state, 'in_progress')

        # Add packaging material
        pack_mat = self.env['agx.packaging.material'].create({
            'name': 'Test Carton 15kg',
            'material_type': 'carton',
            'standard_unit_cost': 2.5,
            'uom_id': self.env.ref('uom.product_uom_unit').id,
        })
        self.env['agx.batch.packaging.line'].create({
            'batch_id': batch.id,
            'material_id': pack_mat.id,
            'qty': 200,
            'unit_cost': 2.5,
        })
        self.assertAlmostEqual(batch.total_packaging_cost, 500.0, places=1)

        # Add scrap
        self.env['agx.batch.scrap'].create({
            'batch_id': batch.id,
            'scrap_type': 'natural_loss',
            'scrap_qty': 150.0,
            'uom_id': self.env.ref('uom.product_uom_kgm').id,
        })
        self.assertAlmostEqual(batch.total_scrap_qty, 150.0, places=1)

    def test_03_shipment_validations_pass(self):
        """Shipment with valid data passes all validations."""
        shp = self.env['agx.shipment'].create({
            'customer_id': self.customer.id,
            'destination_id': self.destination.id,
            'season_id': self.season.id,
            'shipment_date': fields.Date.today(),
            'etd': '2025-12-20',
            'eta': '2025-12-28',
            'container_no': 'TSTU1234567',
            'bl_number': 'BL-FLOW-001',
        })
        fin_line = self.env['agx.shipment.line'].create({
            'shipment_id': shp.id,
            'product_id': self.fin_product.id,
            'product_qty': 100.0,
            'carton_qty': 100.0,
            'net_weight': 1500.0,
            'gross_weight': 1650.0,
        })
        # Should not raise
        shp._validate_before_reserve()
        self.assertEqual(fin_line.uom_id, self.fin_product.uom_id)




    def test_07_season_analytic_account_created(self):
        """Season auto-creates analytic account on save."""
        season = self.env['agx.season'].create({
            'name': 'Analytic Test Season',
            'code': 'ATS-25',
            'crop_category_id': self.crop.id,
            'state': 'active',
        })
        self.assertTrue(
            season.analytic_account_id,
            "Season should auto-create analytic account"
        )
        self.assertIn(season.name, season.analytic_account_id.name)

    def test_08_shipment_so_creates_with_analytic(self):
        """Sale order created from shipment carries analytic distribution."""
        shp = self.env['agx.shipment'].create({
            'customer_id': self.customer.id,
            'season_id': self.season.id,
            'shipment_date': fields.Date.today(),
        })
        shp.action_create_sale_order()
        self.assertTrue(shp.sale_order_id)
        if shp.season_id.analytic_account_id:
            for line in shp.sale_order_id.order_line:
                self.assertIn(
                    str(shp.season_id.analytic_account_id.id),
                    (line.analytic_distribution or {}),
                )

    def test_09_packaging_cost_in_effective_cost(self):
        """Packaging cost is included in effective_allocable_cost."""
        batch = self.env['agx.batch'].create({
            'batch_date': '2025-12-01',
            'season_id': self.season.id,
            'manual_operation_cost': 1000.0,
        })
        pack_mat = self.env['agx.packaging.material'].create({
            'name': 'Test Pallet', 'material_type': 'pallet',
            'standard_unit_cost': 50.0,
            'uom_id': self.env.ref('uom.product_uom_unit').id,
        })
        self.env['agx.batch.packaging.line'].create({
            'batch_id': batch.id,
            'material_id': pack_mat.id,
            'qty': 10, 'unit_cost': 50.0,
        })
        # effective = manual_op + manual_other + packaging (no actual receipts)
        expected = 1000.0 + 500.0  # operation + packaging
        self.assertAlmostEqual(batch.effective_allocable_cost, expected, places=0)

    def test_10_intercompany_so_link(self):
        """Intercompany SO can be linked to evaluation."""
        ev = self.env['agx.evaluation'].create({
            'farm_partner_id': self.vendor.id,
            'partner_id': self.vendor.id,
            'crop_category_id': self.crop.id,
            'season_id': self.season.id,
            'evaluation_date': '2025-11-01',
            'farm_expected_qty': 1000.0,
        })
        so = self.env['sale.order'].create({
            'partner_id': self.customer.id,
        })
        ev.intercompany_so_id = so.id
        self.assertEqual(ev.intercompany_so_id.id, so.id)
        # Can retrieve back from evaluation
        ev2 = self.env['agx.evaluation'].browse(ev.id)
        self.assertEqual(ev2.intercompany_so_id, so)
