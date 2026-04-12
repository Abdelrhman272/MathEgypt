from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAgxStockFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Picking = cls.env["stock.picking"]
        cls.Move = cls.env["stock.move"]
        cls.MoveLine = cls.env["stock.move.line"]
        cls.Batch = cls.env["agx.batch"]
        cls.Shipment = cls.env["agx.shipment"]
        cls.Evaluation = cls.env["agx.evaluation"]

        cls.partner_vendor = cls.env["res.partner"].create({"name": "AGX Vendor"})
        cls.partner_customer = cls.env["res.partner"].create({"name": "AGX Customer"})

        cls.farm = cls.env["agx.farm"].create({
            "name": "AGX Farm",
            "partner_id": cls.partner_vendor.id,
            "company_id": cls.company.id,
        })
        cls.grade = cls.env["agx.grade"].create({"name": "G1"})
        cls.size = cls.env["agx.size"].create({"name": "50", "number": 50})

        cls.raw_location = cls.env["stock.location"].create({
            "name": "AGX Raw",
            "usage": "internal",
            "company_id": cls.company.id,
        })
        cls.production_location = cls.env["stock.location"].create({
            "name": "AGX Prod",
            "usage": "production",
            "company_id": cls.company.id,
        })
        cls.finished_location = cls.env["stock.location"].create({
            "name": "AGX Finished",
            "usage": "internal",
            "company_id": cls.company.id,
        })

        cls.incoming_type = cls.env["stock.picking.type"].search([
            ("code", "=", "incoming"),
            ("company_id", "in", [False, cls.company.id]),
        ], limit=1, order="company_id desc,id")
        cls.internal_type = cls.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            ("company_id", "in", [False, cls.company.id]),
        ], limit=1, order="company_id desc,id")
        cls.outgoing_type = cls.env["stock.picking.type"].search([
            ("code", "=", "outgoing"),
            ("company_id", "in", [False, cls.company.id]),
        ], limit=1, order="company_id desc,id")

        cls.company.write({
            "agx_raw_material_location_id": cls.raw_location.id,
            "agx_production_location_id": cls.production_location.id,
            "agx_finished_goods_location_id": cls.finished_location.id,
            "agx_internal_picking_type_id": cls.internal_type.id,
            "agx_outgoing_picking_type_id": cls.outgoing_type.id,
            "agx_auto_generate_lot_numbers": False,
        })

        cls.raw_product = cls.env["product.product"].create({
            "name": "AGX Raw Product",
            "type": "product",
            "uom_id": cls.env.ref("uom.product_uom_kgm").id,
            "purchase_ok": True,
        })
        cls.finished_product = cls.env["product.product"].create({
            "name": "AGX Finished Product",
            "type": "product",
            "tracking": "lot",
            "uom_id": cls.env.ref("uom.product_uom_kgm").id,
            "purchase_ok": True,
        })

    @classmethod
    def _set_ml_qty(cls, move_line, qty):
        qty_field = "quantity" if "quantity" in move_line._fields else "qty_done"
        move_line[qty_field] = qty

    @classmethod
    def _validate_picking(cls, picking):
        result = picking.with_context(skip_immediate=True, skip_backorder=True).button_validate()
        if isinstance(result, dict):
            pending_moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
            if pending_moves:
                pending_moves._action_done()

    def _create_done_incoming(self, product, qty, dest_location, partner=None, purchase=False, lot=False):
        partner = partner or self.partner_vendor
        picking = self.Picking.create({
            "picking_type_id": self.incoming_type.id,
            "partner_id": partner.id,
            "company_id": self.company.id,
            "location_id": self.incoming_type.default_location_src_id.id,
            "location_dest_id": dest_location.id,
            "purchase_id": purchase.id if purchase else False,
        })
        move = self.Move.create({
            "name": f"IN {product.display_name}",
            "company_id": self.company.id,
            "product_id": product.id,
            "product_uom_qty": qty,
            "product_uom": product.uom_id.id,
            "location_id": picking.location_id.id,
            "location_dest_id": picking.location_dest_id.id,
            "picking_id": picking.id,
        })
        picking.action_confirm()
        ml_vals = {
            "move_id": move.id,
            "product_id": product.id,
            "product_uom_id": product.uom_id.id,
            "location_id": picking.location_id.id,
            "location_dest_id": picking.location_dest_id.id,
            "lot_id": lot.id if lot else False,
        }
        move_line = self.MoveLine.create(ml_vals)
        self._set_ml_qty(move_line, qty)
        self._validate_picking(picking)
        return picking

    def _create_evaluation(self):
        return self.Evaluation.create({
            "farm_id": self.farm.id,
            "partner_id": self.partner_vendor.id,
            "company_id": self.company.id,
            "farm_expected_qty": 10.0,
            "line_ids": [(0, 0, {
                "product_id": self.raw_product.id,
                "grade_id": self.grade.id,
                "size_id": self.size.id,
                "expected_ratio": 100.0,
                "uom_id": self.raw_product.uom_id.id,
                "estimated_unit_price": 10.0,
            })],
        })

    def test_incoming_receipt_for_agx_purchase_goes_to_raw_location(self):
        evaluation = self._create_evaluation()
        po = self.env["purchase.order"].create({
            "partner_id": self.partner_vendor.id,
            "company_id": self.company.id,
            "agx_evaluation_id": evaluation.id,
            "order_line": [(0, 0, {
                "name": self.raw_product.display_name,
                "product_id": self.raw_product.id,
                "product_qty": 5.0,
                "product_uom": self.raw_product.uom_id.id,
                "price_unit": 10.0,
                "date_planned": fields.Datetime.now(),
            })],
        })
        wrong_dest = self.finished_location
        picking = self.Picking.create({
            "picking_type_id": self.incoming_type.id,
            "partner_id": self.partner_vendor.id,
            "company_id": self.company.id,
            "purchase_id": po.id,
            "location_id": self.incoming_type.default_location_src_id.id,
            "location_dest_id": wrong_dest.id,
        })
        move = self.Move.create({
            "name": self.raw_product.display_name,
            "company_id": self.company.id,
            "product_id": self.raw_product.id,
            "product_uom_qty": 5.0,
            "product_uom": self.raw_product.uom_id.id,
            "location_id": picking.location_id.id,
            "location_dest_id": wrong_dest.id,
            "picking_id": picking.id,
        })
        self.MoveLine.create({
            "move_id": move.id,
            "product_id": self.raw_product.id,
            "product_uom_id": self.raw_product.uom_id.id,
            "location_id": picking.location_id.id,
            "location_dest_id": wrong_dest.id,
        })
        picking.write({"origin": "AGX TEST"})
        self.assertEqual(picking.location_dest_id, self.raw_location)
        self.assertTrue(all(m.location_dest_id == self.raw_location for m in picking.move_ids))
        self.assertTrue(all(ml.location_dest_id == self.raw_location for ml in picking.move_line_ids))

    def test_batch_consumption_and_output_use_configured_locations(self):
        evaluation = self._create_evaluation()
        lot_out = self.env["stock.lot"].create({
            "name": "AGX-OUT-LOT",
            "product_id": self.finished_product.id,
            "company_id": self.company.id,
        })
        self._create_done_incoming(self.raw_product, 8.0, self.raw_location, partner=self.partner_vendor)
        batch = self.Batch.create({
            "evaluation_id": evaluation.id,
            "company_id": self.company.id,
            "input_line_ids": [(0, 0, {
                "product_id": self.raw_product.id,
                "qty": 5.0,
                "uom_id": self.raw_product.uom_id.id,
            })],
            "output_line_ids": [(0, 0, {
                "product_id": self.finished_product.id,
                "lot_id": lot_out.id,
                "qty": 5.0,
                "uom_id": self.finished_product.uom_id.id,
            })],
        })
        batch.action_done()
        consume_moves = self.Move.search([("agx_batch_id", "=", batch.id), ("agx_flow_type", "=", "consume")])
        output_moves = self.Move.search([("agx_batch_id", "=", batch.id), ("agx_flow_type", "=", "output")])
        self.assertTrue(consume_moves, "Expected consumption stock moves to be created.")
        self.assertTrue(output_moves, "Expected output stock moves to be created.")
        self.assertTrue(all(m.location_dest_id == self.production_location for m in consume_moves))
        self.assertTrue(all(m.location_id == self.production_location and m.location_dest_id == self.finished_location for m in output_moves))

    def test_shipment_blocked_when_reservation_incomplete(self):
        lot = self.env["stock.lot"].create({
            "name": "AGX-SHP-LOT-A",
            "product_id": self.finished_product.id,
            "company_id": self.company.id,
        })
        self._create_done_incoming(self.finished_product, 10.0, self.finished_location, partner=self.partner_vendor, lot=lot)
        shipment = self.Shipment.create({
            "company_id": self.company.id,
            "customer_id": self.partner_customer.id,
            "line_ids": [(0, 0, {
                "product_id": self.finished_product.id,
                "product_qty": 10.0,
                "uom_id": self.finished_product.uom_id.id,
            })],
        })
        line = shipment.line_ids[:1]
        self.env["agx.shipment.lot.line"].create({
            "shipment_id": shipment.id,
            "shipment_line_id": line.id,
            "product_id": self.finished_product.id,
            "lot_id": lot.id,
            "reserved_qty": 5.0,
        })
        with self.assertRaises(UserError):
            shipment.action_ship()

    def test_shipment_blocked_with_unlinked_lot_lines(self):
        lot = self.env["stock.lot"].create({
            "name": "AGX-SHP-LOT-B",
            "product_id": self.finished_product.id,
            "company_id": self.company.id,
        })
        self._create_done_incoming(self.finished_product, 6.0, self.finished_location, partner=self.partner_vendor, lot=lot)
        shipment = self.Shipment.create({
            "company_id": self.company.id,
            "customer_id": self.partner_customer.id,
            "line_ids": [(0, 0, {
                "product_id": self.finished_product.id,
                "product_qty": 6.0,
                "uom_id": self.finished_product.uom_id.id,
            })],
        })
        self.env["agx.shipment.lot.line"].create({
            "shipment_id": shipment.id,
            "product_id": self.finished_product.id,
            "lot_id": lot.id,
            "reserved_qty": 6.0,
        })
        with self.assertRaises(UserError):
            shipment.action_ship()
