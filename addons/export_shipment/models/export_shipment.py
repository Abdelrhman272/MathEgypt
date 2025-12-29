# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError


class ExportShipment(models.Model):
    _name = "export.shipment"
    _description = "Export Shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Shipment No", required=True, copy=False, readonly=True, default="New", tracking=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    destination = fields.Char(string="Destination")
    container_no = fields.Char(string="Container No", required=True)
    seal_no = fields.Char(string="Seal No")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("done", "Shipped"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    line_ids = fields.One2many("export.shipment.line", "shipment_id", string="Lines")
    lot_line_ids = fields.One2many("export.shipment.lot.line", "shipment_id", string="Reserved Lots")

    sale_order_id = fields.Many2one("sale.order", string="Sale Order", readonly=True, copy=False)
    picking_id = fields.Many2one("stock.picking", string="Reservation Picking", readonly=True, copy=False)

    total_qty = fields.Float(compute="_compute_total_qty", store=True)

    @api.depends("line_ids.product_uom_qty")
    def _compute_total_qty(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_uom_qty"))

    def _ensure_sequence_exists(self):
        seq = self.env["ir.sequence"].sudo().search([("code", "=", "export.shipment")], limit=1)
        if not seq:
            self.env["ir.sequence"].sudo().create(
                {
                    "name": "Export Shipment Sequence",
                    "code": "export.shipment",
                    "prefix": "EXP/",
                    "padding": 5,
                    "company_id": False,
                }
            )

    def _next_shipment_name(self):
        self._ensure_sequence_exists()
        return self.env["ir.sequence"].next_by_code("export.shipment") or "EXP/00000"

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "name" in fields_list and (not res.get("name") or res.get("name") == "New"):
            res["name"] = self._next_shipment_name()
        return res

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            self._ensure_sequence_exists()
            vals["name"] = self._next_shipment_name()
        return super().create(vals)

    def _sync_reserved_lots_from_picking(self):
        """Sync reserved lots from picking moves to shipment lot lines."""
        self.ensure_one()
        if not self.picking_id:
            return

        # Clear old lines
        self.lot_line_ids.unlink()

        for move in self.picking_id.move_ids_without_package:
            for ml in move.move_line_ids:
                self.env["export.shipment.lot.line"].create(
                    {
                        "shipment_id": self.id,
                        "line_id": False,
                        "product_id": ml.product_id.id,
                        "lot_id": ml.lot_id.id if ml.lot_id else False,
                        "qty": ml.reserved_uom_qty or ml.qty_done or 0.0,
                    }
                )

    def action_mark_reserved(self):
        """Reserve products by creating picking and assigning."""
        for rec in self:
            if rec.state != "draft":
                continue

            if not rec.line_ids:
                raise ValidationError(_("Please add shipment lines before reserving lots."))

            Picking = self.env["stock.picking"]
            Move = self.env["stock.move"]

            picking_type = self.env["stock.picking.type"].search(
                [("code", "=", "internal"), ("warehouse_id.company_id", "=", rec.company_id.id)], limit=1
            )
            if not picking_type:
                picking_type = self.env["stock.picking.type"].search([("code", "=", "internal")], limit=1)

            if not picking_type:
                raise ValidationError(_("No internal picking type found. Please configure Warehouse / Picking Types."))

            if not picking_type.default_location_src_id or not picking_type.default_location_dest_id:
                raise ValidationError(_("Internal picking type must have default source and destination locations."))

            picking = Picking.create(
                {
                    "picking_type_id": picking_type.id,
                    "location_id": picking_type.default_location_src_id.id,
                    "location_dest_id": picking_type.default_location_dest_id.id,
                    "company_id": rec.company_id.id,
                    "origin": rec.name,
                }
            )

            for line in rec.line_ids:
                Move.create(
                    {
                        "name": line.product_id.display_name,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.product_uom_qty,
                        "product_uom": line.product_uom_id.id,
                        "picking_id": picking.id,
                        "location_id": picking.location_id.id,
                        "location_dest_id": picking.location_dest_id.id,
                        "company_id": rec.company_id.id,
                    }
                )

            picking.action_confirm()
            picking.action_assign()

            rec.picking_id = picking.id
            rec.state = "reserved"
            rec._sync_reserved_lots_from_picking()

    # -------------------------
    # METHODS REQUIRED BY VIEW BUTTONS
    # -------------------------
    def action_reserve_lots(self):
        """Compatibility method used by the form button."""
        return self.action_mark_reserved()

    def action_unreserve(self):
        """Unreserve lots and reset shipment back to Draft."""
        for rec in self:
            picking = rec.picking_id
            if picking and picking.state not in ("done", "cancel"):
                # In Odoo 19, do_unreserve is commonly available. If not, fallback to cancel.
                if hasattr(picking, "do_unreserve"):
                    picking.do_unreserve()
                else:
                    picking.action_cancel()

            if rec.lot_line_ids:
                rec.lot_line_ids.unlink()

            rec.picking_id = False
            rec.state = "draft"

    def action_create_sale_order(self):
        """Create a Sale Order **by Container** (not by products).

        Business rule:
        - SO/Invoice must be by CONTAINER (commercial doc)
        - Inventory reservation & delivery stays in Export Shipment using real products

        What this does:
        - Create SO once
        - Write container details on SO header (custom fields)
        - Add ONE service line "Export Container" with clear container details
        """
        SaleOrder = self.env["sale.order"]
        SaleOrderLine = self.env["sale.order.line"]

        # Service product representing the container on commercial docs
        tmpl = self.env.ref("export_shipment.product_template_export_container", raise_if_not_found=False)
        container_product = tmpl.product_variant_id if tmpl else False
        if not container_product:
            raise ValidationError(
                _("Missing container service product. Please update the module to load the product data.")
            )

        for rec in self:
            if rec.sale_order_id:
                continue

            so_vals = {
                "partner_id": rec.partner_id.id,
                "company_id": rec.company_id.id,
                "origin": rec.name,
            }
            if rec.partner_id.property_product_pricelist:
                so_vals["pricelist_id"] = rec.partner_id.property_product_pricelist.id

            so = SaleOrder.create(so_vals)

            # Save container details on the SO header (easy visibility)
            so.write({
                "x_export_shipment_id": rec.id,
                "x_container_no": rec.container_no,
                "x_seal_no": rec.seal_no,
                "x_destination": rec.destination,
            })

            # ONE line only (container service line)
            line_desc = (
                f"Shipment: {rec.name}\n"
                f"Container No: {rec.container_no or ''}\n"
                f"Seal No: {rec.seal_no or ''}\n"
                f"Destination: {rec.destination or ''}\n"
            )

            SaleOrderLine.create({
                "order_id": so.id,
                "product_id": container_product.id,
                "product_uom_qty": 1.0,
                "name": line_desc,
            })

            rec.sale_order_id = so

            return {
                "type": "ir.actions.act_window",
                "res_model": "sale.order",
                "res_id": so.id,
                "view_mode": "form",
                "target": "current",
            }

    def action_validate_shipment(self):
        for rec in self:
            if rec.state != "reserved":
                continue
            rec.state = "done"

    def action_cancel(self):
        for rec in self:
            rec.state = "cancel"

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"


class ExportShipmentLine(models.Model):
    _name = "export.shipment.line"
    _description = "Export Shipment Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    product_uom_qty = fields.Float(string="Quantity", required=True, default=1.0)
    product_uom_id = fields.Many2one("uom.uom", string="UoM", required=True)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.product_uom_id = rec.product_id.uom_id.id


class ExportShipmentLotLine(models.Model):
    _name = "export.shipment.lot.line"
    _description = "Export Shipment Lot Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    line_id = fields.Many2one("export.shipment.line", string="Related Line")
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial")
    qty = fields.Float(required=True, default=1.0)


class SaleOrder(models.Model):
    """Extend Sale Order to show container details on the header (Odoo 19)."""
    _inherit = "sale.order"

    x_export_shipment_id = fields.Many2one("export.shipment", string="Export Shipment", copy=False, readonly=True)
    x_container_no = fields.Char(string="Container No", copy=False, readonly=True)
    x_seal_no = fields.Char(string="Seal No", copy=False, readonly=True)
    x_destination = fields.Char(string="Destination", copy=False, readonly=True)
