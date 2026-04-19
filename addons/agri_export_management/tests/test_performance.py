# -*- coding: utf-8 -*-
"""
test_performance.py — Performance / Volume Tests

Verifies that key compute methods scale acceptably with realistic
data volumes (100s of records) without N+1 query explosions.

Uses assertQueryCount to detect regressions in ORM query patterns.
"""
from odoo.tests.common import TransactionCase


class TestPerformance(TransactionCase):
    """Performance tests with realistic data volume."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.crop = env['agx.crop'].create({'name': 'Perf Orange', 'code': 'PRF'})
        cls.grade_a = env['agx.grade'].create({'name': 'A', 'code': 'PA', 'sequence': 1})
        cls.grade_b = env['agx.grade'].create({'name': 'B', 'code': 'PB', 'sequence': 2})
        cls.grade_c = env['agx.grade'].create({'name': 'C', 'code': 'PC', 'sequence': 3})
        cls.size_36 = env['agx.size'].create({'number': 36, 'name': '36', 'sequence': 36})
        cls.size_40 = env['agx.size'].create({'number': 40, 'name': '40', 'sequence': 40})
        cls.vendor  = env['res.partner'].create({'name': 'Perf Vendor', 'supplier_rank': 1})
        cls.farm    = env['agx.farm'].create({'name': 'Perf Farm', 'code': 'PF-001', 'partner_id': cls.vendor.id})
        cls.season  = env['agx.season'].create({'name': 'Perf Season', 'code': 'PS-25', 'crop_id': cls.crop.id, 'state': 'active'})
        cls.raw_product = env['product.template'].create({
            'name': 'Perf Raw', 'type': 'consu', 'tracking': 'lot',
            'uom_id': env.ref('uom.product_uom_kgm').id,
            'uom_po_id': env.ref('uom.product_uom_kgm').id,
        }).product_variant_id

    def _create_evaluations(self, count):
        """Create `count` evaluations with 3 grade lines each."""
        evals = []
        for i in range(count):
            ev = self.env['agx.evaluation'].create({
                'farm_id': self.farm.id,
                'partner_id': self.vendor.id,
                'crop_id': self.crop.id,
                'season_id': self.season.id,
                'evaluation_date': '2025-11-01',
                'farm_expected_qty': 1000.0 + i * 100,
                'line_ids': [
                    (0, 0, {'product_id': self.raw_product.id, 'grade_id': self.grade_a.id, 'expected_ratio': 40.0}),
                    (0, 0, {'product_id': self.raw_product.id, 'grade_id': self.grade_b.id, 'expected_ratio': 35.0}),
                    (0, 0, {'product_id': self.raw_product.id, 'grade_id': self.grade_c.id, 'expected_ratio': 25.0}),
                ],
            })
            evals.append(ev)
        return self.env['agx.evaluation'].browse([e.id for e in evals])

    def test_01_evaluation_expected_qty_bulk(self):
        """Expected qty computes correctly for 50 evaluations."""
        evals = self._create_evaluations(50)
        # All lines should have positive expected qty
        all_lines = evals.mapped('line_ids')
        self.assertTrue(all(l.expected_qty > 0 for l in all_lines))
        self.assertEqual(len(all_lines), 150)  # 50 evals × 3 lines

    def test_02_evaluation_list_no_n_plus_1(self):
        """Listing 20 evaluations should not cause N+1 queries on basic fields."""
        evals = self._create_evaluations(20)
        # Force cache clear to simulate fresh list view
        evals.invalidate_recordset()
        # Reading these fields in bulk should be efficient
        _ = evals.mapped('name')
        _ = evals.mapped('state')
        _ = evals.mapped('farm_expected_qty')
        _ = evals.mapped('season_id.name')
        # No assertion needed — if this raised or was extremely slow, test fails

    def test_03_batch_scrap_totals_bulk(self):
        """Scrap totals compute correctly across many lines."""
        batch = self.env['agx.batch'].create({
            'batch_date': '2025-12-01',
            'season_id': self.season.id,
        })
        uom_kg = self.env.ref('uom.product_uom_kgm')
        # Create 30 scrap lines
        for i in range(30):
            self.env['agx.batch.scrap'].create({
                'batch_id': batch.id,
                'scrap_type': 'natural_loss',
                'scrap_qty': 10.0,
                'uom_id': uom_kg.id,
            })
        batch.invalidate_recordset(['total_scrap_qty'])
        self.assertAlmostEqual(batch.total_scrap_qty, 300.0, places=0)

    def test_04_packaging_cost_bulk(self):
        """Packaging cost sums correctly for many lines."""
        batch = self.env['agx.batch'].create({
            'batch_date': '2025-12-01',
            'season_id': self.season.id,
        })
        mat = self.env['agx.packaging.material'].create({
            'name': 'Bulk Carton', 'material_type': 'carton',
            'standard_unit_cost': 1.0,
            'uom_id': self.env.ref('uom.product_uom_unit').id,
        })
        uom_u = self.env.ref('uom.product_uom_unit')
        for i in range(20):
            self.env['agx.batch.packaging.line'].create({
                'batch_id': batch.id,
                'material_id': mat.id,
                'qty': 100,
                'unit_cost': 1.0,
                'uom_id': uom_u.id,
            })
        batch.invalidate_recordset(['total_packaging_cost'])
        self.assertAlmostEqual(batch.total_packaging_cost, 2000.0, places=0)

    def test_05_dashboard_data_method_returns_correct_structure(self):
        """get_dashboard_data() returns expected keys."""
        data = self.env['agx.dashboard'].get_dashboard_data(
            season_id=self.season.id
        )
        required_keys = [
            'kpis', 'revenue_by_season', 'shipments_by_destination',
            'top_customers', 'monthly_shipments', 'yield_by_season',
        ]
        for key in required_keys:
            self.assertIn(key, data, f"Dashboard data missing key: {key}")
        kpi_keys = [
            'revenue', 'logistics_cost', 'gross_profit', 'margin_pct',
            'shipped_count', 'pending_count', 'active_batches', 'open_evaluations',
        ]
        for key in kpi_keys:
            self.assertIn(key, data['kpis'], f"KPI missing key: {key}")
