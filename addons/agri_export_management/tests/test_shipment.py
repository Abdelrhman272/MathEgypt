# -*- coding: utf-8 -*-
"""
test_shipment.py — Unit tests for AgxShipment business validations
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError
from odoo import fields


class TestAgxShipment(TransactionCase):

    def setUp(self):
        super().setUp()
        self.customer = self.env['res.partner'].create({'name': 'Test Customer NL', 'customer_rank': 1})
        self.dest = self.env['agx.destination'].create({'name': 'Test Netherlands', 'port_name': 'Rotterdam'})
        self.season = self.env['agx.season'].create({
            'name': 'Ship Test Season',
            'code': 'STS-25',
            'state': 'active',
        })

    def _make_shipment(self, **kwargs):
        vals = {
            'customer_id': self.customer.id,
            'destination_id': self.dest.id,
            'season_id': self.season.id,
            'shipment_date': fields.Date.today(),
        }
        vals.update(kwargs)
        return self.env['agx.shipment'].create(vals)

    def test_01_shipment_created_draft(self):
        shp = self._make_shipment()
        self.assertEqual(shp.state, 'draft')

    def test_02_cancel_shipment(self):
        shp = self._make_shipment()
        shp.action_cancel()
        self.assertEqual(shp.state, 'cancelled')

    def test_03_reserve_without_lines_fails(self):
        """Reserve Lots with no product lines should raise UserError."""
        shp = self._make_shipment()
        with self.assertRaises(UserError):
            shp.action_reserve()

    def test_04_etd_past_blocks_reserve(self):
        """ETD in the past should block reservation."""
        shp = self._make_shipment(etd='2020-01-01')
        with self.assertRaises(UserError):
            shp._validate_before_reserve()

    def test_05_eta_before_etd_blocks_reserve(self):
        """ETA before ETD should block reservation."""
        shp = self._make_shipment(etd='2025-12-10', eta='2025-12-05')
        with self.assertRaises(UserError):
            shp._validate_before_reserve()

    def test_06_duplicate_bl_blocks_ship(self):
        """Duplicate B/L number should raise UserError on second shipment."""
        shp1 = self._make_shipment(bl_number='BL-TEST-001')
        shp1.state = 'reserved'
        shp2 = self._make_shipment(bl_number='BL-TEST-001')
        shp2.state = 'reserved'
        with self.assertRaises(UserError):
            shp2._validate_before_ship()

    def test_07_no_bl_posts_chatter_warning(self):
        """Missing B/L number posts a chatter warning (non-blocking)."""
        shp = self._make_shipment()
        shp.state = 'reserved'
        # Should not raise — just post to chatter
        shp._validate_before_ship()
        msgs = shp.message_ids.filtered(lambda m: 'B/L' in (m.body or ''))
        self.assertTrue(msgs, "Warning about missing B/L should appear in chatter")

    def test_08_container_count_creates_containers(self):
        """Creating a shipment auto-creates container records."""
        shp = self._make_shipment()
        self.assertEqual(len(shp.container_ids), shp.container_count)

    def test_09_shipment_line_uom_defaults_from_product(self):
        """Shipment line UoM should default to product's UoM on create."""
        product = self.env['product.template'].create({
            'name': 'Test Packed',
            'type': 'consu',
            'uom_id': self.env.ref('uom.product_uom_unit').id,
            'uom_po_id': self.env.ref('uom.product_uom_unit').id,
        }).product_variant_id
        shp = self._make_shipment()
        line = self.env['agx.shipment.line'].create({
            'shipment_id': shp.id,
            'product_id': product.id,
            'product_qty': 100.0,
            # no uom_id provided — should default from product
        })
        self.assertEqual(line.uom_id, product.uom_id)
