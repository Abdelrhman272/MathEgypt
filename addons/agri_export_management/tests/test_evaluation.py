# -*- coding: utf-8 -*-
"""
test_evaluation.py — Unit tests for AgxEvaluation and AgxEvaluationLine
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestAgxEvaluation(TransactionCase):

    def setUp(self):
        super().setUp()
        company = self.env.company

        self.crop = self.env['product.category'].create({'name': 'Test Orange', 'is_agx_crop': True})
        self.grade_a = self.env['agx.grade'].create({'name': 'A', 'code': 'A', 'sequence': 1})
        self.grade_b = self.env['agx.grade'].create({'name': 'B', 'code': 'B', 'sequence': 2})

        for num in [36, 40]:
            self.env['agx.size'].create({'number': num, 'name': str(num), 'sequence': num})
        self.size_36 = self.env['agx.size'].search([('number', '=', 36)], limit=1)

        self.vendor = self.env['res.partner'].create({
            'name': 'Test Farm Vendor', 'supplier_rank': 1
        })
        self.farm = self.env['res.partner'].create({
            'name': 'Test Farm', 'code': 'TF-001', 'partner_id': self.vendor.id
        })
        self.season = self.env['agx.season'].create({
            'name': 'Test Season 2025', 'code': 'TST-25',
            'crop_category_id': self.crop.id, 'state': 'active',
            'date_start': '2025-11-01',})
        self.raw_product = self.env['product.template'].create({
            'name': 'Test Raw Product', 'type': 'consu',
            'tracking': 'lot',
            'uom_id': self.env.ref('uom.product_uom_kgm').id,
            'uom_po_id': self.env.ref('uom.product_uom_kgm').id,
        }).product_variant_id

    def _make_eval(self, qty=1000.0):
        return self.env['agx.evaluation'].create({
            'farm_partner_id': self.vendor.id,
            'partner_id': self.vendor.id,
            'crop_category_id': self.crop.id,
            'season_id': self.season.id,
            'evaluation_date': '2025-11-01',
            'farm_expected_qty': qty,
            'line_ids': [
                (0, 0, {
                    'product_id': self.raw_product.id,
                    'grade_id': self.grade_a.id,
                    'expected_ratio': 60.0,
                    'estimated_unit_price': 2.5,
                }),
                (0, 0, {
                    'product_id': self.raw_product.id,
                    'grade_id': self.grade_b.id,
                    'expected_ratio': 40.0,
                    'estimated_unit_price': 2.0,
                }),
            ],
        })

    def test_01_create_evaluation(self):
        """Evaluation is created in draft state."""
        ev = self._make_eval()
        self.assertEqual(ev.state, 'draft')
        self.assertEqual(len(ev.line_ids), 2)

    def test_02_expected_qty_computation(self):
        """Expected qty = farm_expected_qty × ratio / 100."""
        ev = self._make_eval(qty=1000.0)
        line_a = ev.line_ids.filtered(lambda l: l.grade_id == self.grade_a)
        self.assertAlmostEqual(line_a.expected_qty, 600.0, places=1)

    def test_03_approve(self):
        """Approved state reachable from draft."""
        ev = self._make_eval()
        ev.action_approve()
        self.assertEqual(ev.state, 'approved')

    def test_04_reset_to_draft(self):
        """Reset to draft from approved."""
        ev = self._make_eval()
        ev.action_approve()
        ev.action_reset_draft()
        self.assertEqual(ev.state, 'draft')

    def test_05_cancel(self):
        """Cancel from draft."""
        ev = self._make_eval()
        ev.action_cancel()
        self.assertEqual(ev.state, 'cancelled')

    def test_06_create_po_requires_approved(self):
        """Create PO requires approved state."""
        ev = self._make_eval()
        with self.assertRaises(UserError):
            ev.action_create_purchase_order()

    def test_07_create_po_creates_record(self):
        """Create PO creates a purchase.order record."""
        ev = self._make_eval()
        ev.action_approve()
        ev.action_create_purchase_order()
        self.assertTrue(ev.po_id, "PO should be created after action")
        self.assertEqual(ev.state, 'po_created')

    def test_08_achievement_pct_zero_before_batch(self):
        """Actual qty and achievement % are 0 before any batch."""
        ev = self._make_eval()
        ev.action_approve()
        for line in ev.line_ids:
            self.assertEqual(line.actual_qty, 0.0)
            self.assertEqual(line.achievement_pct, 0.0)

    def test_09_intercompany_so_linkable(self):
        """intercompany_so_id is settable on evaluation."""
        ev = self._make_eval()
        so = self.env['sale.order'].create({
            'partner_id': self.vendor.id,
        })
        ev.intercompany_so_id = so.id
        self.assertEqual(ev.intercompany_so_id, so)
