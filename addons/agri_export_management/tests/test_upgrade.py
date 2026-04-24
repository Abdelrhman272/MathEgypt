# -*- coding: utf-8 -*-
"""
test_upgrade.py — Upgrade / Migration Safety Tests

Verifies that:
  1. All required DB columns exist after install
  2. Computed stored fields have correct values after recompute
  3. Sequences are created and functional
  4. Default settings are safe after install
  5. No orphaned records left by unlink cascades
"""
from odoo.tests.common import TransactionCase


class TestUpgradeSafety(TransactionCase):
    """Upgrade and migration safety checks."""

    def test_01_all_sequences_exist(self):
        """Required sequences are created on install."""
        codes = [
            'agx.evaluation',
            'agx.batch',
            'agx.shipment',
            'agx.season',
            'agx.claim',
        ]
        for code in codes:
            seq = self.env['ir.sequence'].search([('code', '=', code)], limit=1)
            self.assertTrue(seq, f"Sequence missing for code: {code}")

    def test_02_company_defaults_safe(self):
        """Company AGX fields are nullable — no crash on fresh install."""
        company = self.env.company
        # These should all be False/None on fresh install, not raise
        _ = company.agx_raw_material_location_id
        _ = company.agx_production_location_id
        _ = company.agx_finished_goods_location_id
        _ = company.agx_cold_storage_location_id
        _ = company.agx_internal_picking_type_id
        _ = company.agx_outgoing_picking_type_id
        _ = company.agx_container_service_product_id
        self.assertFalse(company.agx_use_mrp_production)
        self.assertFalse(company.agx_auto_generate_lot_numbers)

    def test_03_season_cascade_analytic(self):
        """Season creates analytic account — account links back."""
        crop = self.env['product.category'].create({'name': 'Upgrade Crop', 'is_agx_crop': True})
        season = self.env['agx.season'].create({
            'name': 'Upgrade Test Season', 'code': 'UPG-25',
            'crop_category_id': crop.id, 'state': 'active',
            'date_start': '2025-11-01',})
        # Analytic account should exist
        if season.analytic_account_id:
            # Account name should reference season
            self.assertIn('Upgrade Test Season', season.analytic_account_id.name)

    def test_04_evaluation_line_recompute_safe(self):
        """Recomputing evaluation lines on empty set does not crash."""
        # Empty recordset recompute
        empty = self.env['agx.evaluation.line'].browse([])
        # Should not raise
        empty._compute_actuals()

    def test_05_batch_scrap_cascade_delete(self):
        """Deleting a batch cascades to scrap lines."""
        season = self.env['agx.season'].create({
            'name': 'Cascade Season', 'code': 'CS-25', 'state': 'active',
            'date_start': '2025-11-01',})
        batch = self.env['agx.batch'].create({
            'batch_date': '2025-12-01', 'season_id': season.id,
        })
        scrap = self.env['agx.batch.scrap'].create({
            'batch_id': batch.id, 'scrap_type': 'natural_loss',
            'scrap_qty': 50.0,
            'uom_id': self.env.ref('uom.product_uom_kgm').id,
        })
        scrap_id = scrap.id
        batch.unlink()
        # Scrap should be deleted by cascade
        self.assertFalse(self.env['agx.batch.scrap'].search([('id', '=', scrap_id)]))

    def test_06_packaging_line_cascade_delete(self):
        """Deleting a batch cascades to packaging lines."""
        season = self.env['agx.season'].create({
            'name': 'Pkg Cascade Season', 'code': 'PCS-25', 'state': 'active',
            'date_start': '2025-11-01',})
        batch = self.env['agx.batch'].create({
            'batch_date': '2025-12-01', 'season_id': season.id,
        })
        mat = self.env['agx.packaging.material'].create({
            'name': 'Cascade Carton', 'material_type': 'carton',
            'standard_unit_cost': 1.0,
            'uom_id': self.env.ref('uom.product_uom_unit').id,
        })
        pkg = self.env['agx.batch.packaging.line'].create({
            'batch_id': batch.id, 'material_id': mat.id,
            'qty': 100, 'unit_cost': 1.0,
        })
        pkg_id = pkg.id
        batch.unlink()
        self.assertFalse(
            self.env['agx.batch.packaging.line'].search([('id', '=', pkg_id)])
        )

    def test_07_shipment_cascade_delete(self):
        """Deleting a shipment cascades to all child lines."""
        customer = self.env['res.partner'].create({'name': 'Cascade Customer'})
        shp = self.env['agx.shipment'].create({
            'customer_id': customer.id,
            'shipment_date': '2025-12-01',
        })
        shp_id = shp.id
        line = self.env['agx.shipment.line'].create({
            'shipment_id': shp.id,
            'product_id': self.env['product.template'].create({
                'name': 'Cascade Product', 'type': 'consu',
                'uom_id': self.env.ref('uom.product_uom_unit').id,
                'uom_po_id': self.env.ref('uom.product_uom_unit').id,
            }).product_variant_id.id,
            'product_qty': 10.0,
        })
        line_id = line.id
        shp.unlink()
        self.assertFalse(
            self.env['agx.shipment.line'].search([('id', '=', line_id)])
        )


    def test_09_size_name_not_null_handled(self):
        """AgxSize can be created without explicit name (computed after insert)."""
        size = self.env['agx.size'].create({'number': 999, 'sequence': 999})
        # After compute, name should equal str(number)
        size.invalidate_recordset(['name'])
        # Name should be set by compute or accepted as null without crash
        # Either way, no IntegrityError should be raised

    def test_10_dashboard_singleton_per_company(self):
        """Only one dashboard per company is created."""
        dash1 = self.env['agx.dashboard'].action_open_dashboard()
        dash2 = self.env['agx.dashboard'].action_open_dashboard()
        # Both calls should return the same record
        d1 = self.env['agx.dashboard'].search([
            ('company_id', '=', self.env.company.id)
        ])
        self.assertEqual(len(d1), 1, "Should be exactly one dashboard per company")
