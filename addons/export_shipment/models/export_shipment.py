# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ExportShipment(models.Model):
    _name = "export.shipment"
    _description = "Export Shipment / Container"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Shipment No",
        required=True,
        copy=False,
        default=lambda self: _("New"),
        tracking=True,
        readonly=True,  # IMPORTANT: الرقم بيتولد تلقائيًا
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True)

    container_no = fields.Char(string="Container No", tracking=True)
    destination = fields.Char(string="Destination", tracking=True)
    seal_no = fields.Char(string="Seal No", tracking=True)

    reserved_picking_id = fields.Many2one("stock.picking", string="Reservation Picking", copy=False, tracking=True)
    sale_order_id = fields.Many2one("sale.order", string="Sale Order", copy=False, tracking=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("shipped", "Shipped"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        required=True,
    )

    line_ids = fields.One2many("export.shipment.line", "shipment_id", string="Lines", copy=True)
    lot_line_ids = fields.One2many("export.shipment.lot.line", "shipment_id", string="Reserved Lots", copy=True)

    total_qty = fields.Float(string="Total Qty", compute="_compute_total_qty", store=True)

    @api.depends("line_ids.product_uom_qty")
    def _compute_total_qty(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_uom_qty"))

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    def _ensure_sequence(self):
        """Guarantee Shipment number exists (even if user didn't save before actions)."""
        for rec in self:
            if rec.name in (False, "New", _("New")):
                rec.name = rec.env["ir.sequence"].next_by_code("export.shipment") or "New"

    @api.model_create_multi
    def create(self, vals_list):
        """Assign Shipment No automatically."""
        for vals in vals_list:
            if vals.get("name") in (False, "New", _("New")):
                vals["name"] = self.env["ir.sequence"].next_by_code("export.shipment") or "New"
        return super().create(vals_list)

    def _get_default_warehouse(self):
        self.ensure_one()
        wh = self.env["stock.warehouse"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not wh:
            wh = self.env["stock.warehouse"].search([], limit=1)
        if not wh:
            raise UserError(_("No warehouse found to create reservation picking."))
        return wh

    def _validate_ready_to_reserve(self):
        self.ensure_one()

        if not self.id:
            # لازم Save الأول عشان lines تبقى records وتقدر تختارها في Related Line
            raise UserError(_("Please save the shipment first, then add Products and Reserved Lots."))

        if self.state != "draft":
            raise UserError(_("Reservation is allowed only in Draft state."))

        if not self.line_ids:
            raise UserError(_("Add shipment lines first."))

        if not self.lot_line_ids:
            raise UserError(_("Add reserved lots first."))

        # strict consistency
        for lot_line in self.lot_line_ids:
            if not lot_line.line_id:
                raise UserError(_("Each reserved lot must be linked to a shipment line."))
            if lot_line.line_id.shipment_id != self:
                raise UserError(_("Related Line must belong to the same Shipment."))
            if lot_line.product_id and lot_line.line_id.product_id != lot_line.product_id:
                raise UserError(_("Reserved lot product must match shipment line product."))
            if lot_line.qty <= 0:
                raise UserError(_("Reserved Qty must be greater than zero."))

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------
    def action_reserve_lots(self):
        """Create outgoing picking and reserve selected lots (NO qty_done here)."""
        for rec in self:
            rec._ensure_sequence()
            rec._validate_ready_to_reserve()

            if rec.reserved_picking_id:
                raise UserError(_("Reservation picking already exists."))

            wh = rec._get_default_warehouse()
            picking_type = wh.out_type_id

            location_src = picking_type.default_location_src_id
            location_dest = picking_type.default_location_dest_id
            if not location_src or not location_dest:
                raise UserError(_("Source/Destination locations are not properly configured."))

            picking = self.env["stock.picking"].create(
                {
                    "picking_type_id": picking_type.id,
                    "company_id": rec.company_id.id,
                    "partner_id": rec.partner_id.id,
                    "origin": rec.name,
                    "location_id": location_src.id,
                    "location_dest_id": location_dest.id,
                    "move_type": "direct",
                }
            )

            # One move per product/uom (demand = sum of reserved lots)
            moves_by_key = {}
            for lot_line in rec.lot_line_ids:
                key = (lot_line.product_id.id, lot_line.product_uom_id.id)
                moves_by_key.setdefault(key, 0.0)
                moves_by_key[key] += lot_line.qty

            move_records = {}
            for (product_id, uom_id), demand_qty in moves_by_key.items():
                move = self.env["stock.move"].create(
                    {
                        "picking_id": picking.id,
                        "company_id": rec.company_id.id,
                        "product_id": product_id,
                        "product_uom_qty": demand_qty,
                        "product_uom": uom_id,
                        "location_id": location_src.id,
                        "location_dest_id": location_dest.id,
                    }
                )
                move_records[(product_id, uom_id)] = move

            # Confirm first to allow reservation
            picking.action_confirm()

            # ✅ FIX: لو ال Picking Type بيعمل reserve تلقائي عند التأكيد
            # امسح أي Reservation اتعملت تلقائيًا قبل الحجز اليدوي
            try:
                picking.do_unreserve()
            except Exception:
                # في بعض النسخ قد تختلف
                picking.move_ids._do_unreserve()

            # Reserve exact lots (strict)
            for lot_line in rec.lot_line_ids:
                move = move_records.get((lot_line.product_id.id, lot_line.product_uom_id.id))
                if not move:
                    continue
                move._update_reserved_quantity(
                    lot_line.qty,
                    location_src,
                    lot_id=lot_line.lot_id,
                    strict=True,
                )

            # IMPORTANT: do NOT call action_assign after manual reservation (avoids double reservation)
            picking.move_ids._recompute_state()

            # OPTIONAL: show assigned state if fully reserved (UI helpful)
            fully_reserved = True
            for mv in picking.move_ids:
                demand = mv.product_uom_qty or 0.0
                reserved = getattr(mv, "reserved_availability", 0.0) or 0.0
                if demand and reserved + 1e-6 < demand:
                    fully_reserved = False
                    break
            if fully_reserved:
                picking.state = "assigned"

            rec.reserved_picking_id = picking.id
            rec.state = "reserved"

    def action_unreserve(self):
        for rec in self:
            if rec.state != "reserved":
                raise UserError(_("Unreserve is allowed only in Reserved state."))
            if rec.reserved_picking_id:
                rec.reserved_picking_id.action_cancel()
                rec.reserved_picking_id.unlink()
            rec.reserved_picking_id = False
            rec.state = "draft"

    def action_create_sale_order(self):
        """Create SO with 1 service line (container booking)."""
        for rec in self:
            rec._ensure_sequence()

            if rec.sale_order_id:
                raise UserError(_("Sale Order already created."))

            if not rec.partner_id:
                raise UserError(_("Set the customer first."))

            addr = rec.partner_id.address_get(["invoice", "delivery"])
            partner_invoice_id = addr.get("invoice") or rec.partner_id.id
            partner_shipping_id = addr.get("delivery") or rec.partner_id.id

            container_tmpl_id = int(
                self.env["ir.config_parameter"].sudo().get_param("export_shipment.container_product_tmpl_id", "0") or 0
            )
            tmpl = self.env["product.template"].browse(container_tmpl_id).exists() if container_tmpl_id else False
            if not tmpl:
                tmpl = self.env.ref("export_shipment.product_tmpl_export_container_service", raise_if_not_found=False)
            if not tmpl:
                raise UserError(
                    _(
                        "Container service product is not configured. Please set it in Inventory > Export > Configuration > Export & Logistics."
                    )
                )

            product = tmpl.product_variant_id
            line_name = rec._build_container_so_line_description()

            so = self.env["sale.order"].create(
                {
                    "partner_id": rec.partner_id.id,
                    "partner_invoice_id": partner_invoice_id,
                    "partner_shipping_id": partner_shipping_id,
                    "company_id": rec.company_id.id,
                    "origin": rec.name,
                    "export_shipment_id": rec.id,
                }
            )

            self.env["sale.order.line"].create(
                {
                    "order_id": so.id,
                    "product_id": product.id,
                    "product_uom_qty": 1.0,
                    "product_uom_id": product.uom_id.id,
                    "name": line_name,
                }
            )

            rec.sale_order_id = so.id

    def action_validate_shipment(self):
        """Validate (ship) from Export Shipment screen:
        - ensure reserved
        - fill qty_done from reserved quantities (move lines only)
        - validate + auto wizards
        """
        for rec in self:
            if not rec.reserved_picking_id:
                raise UserError(_("Reserve lots first."))
            if rec.state != "reserved":
                raise UserError(_("Shipment must be Reserved before validation."))

            picking = rec.reserved_picking_id

            # If already done/cancelled, just mark shipped
            if picking.state in ("done", "cancel"):
                rec.state = "shipped"
                continue

            # Ensure reservation is applied
            # NOTE: you already reserved manually, but assign keeps UI consistent
            try:
                picking.action_assign()
            except Exception:
                pass

            # -----------------------------------------------------------------
            # ✅ CRITICAL FIX:
            # Set qty_done on move lines ONLY (never on stock.move.quantity_done)
            # -----------------------------------------------------------------
            for mv in picking.move_ids:
                if mv.move_line_ids:
                    # Tracked products: move lines exist (with lots)
                    for ml in mv.move_line_ids:
                        if (ml.qty_done or 0.0) == 0.0:
                            reserved = (
                                getattr(ml, "reserved_uom_qty", 0.0)
                                or getattr(ml, "reserved_qty", 0.0)
                                or 0.0
                            )
                            if reserved:
                                ml.qty_done = reserved
                else:
                    # Non-tracked: create one move line and set qty_done there
                    if (mv.product_uom_qty or 0.0) > 0.0:
                        self.env["stock.move.line"].create({
                            "move_id": mv.id,
                            "picking_id": picking.id,
                            "company_id": picking.company_id.id,
                            "product_id": mv.product_id.id,
                            "product_uom_id": mv.product_uom.id,
                            "location_id": mv.location_id.id,
                            "location_dest_id": mv.location_dest_id.id,
                            "qty_done": mv.product_uom_qty,
                        })

            # Validate and auto-process wizards
            res = picking.button_validate()

            if isinstance(res, dict) and res.get("res_model") and res.get("res_id"):
                wizard = self.env[res["res_model"]].browse(res["res_id"]).exists()
                if wizard:
                    if res["res_model"] == "stock.immediate.transfer":
                        wizard.process()
                    elif res["res_model"] == "stock.backorder.confirmation":
                        if hasattr(wizard, "process"):
                            wizard.process()
                        elif hasattr(wizard, "process_cancel_backorder"):
                            wizard.process_cancel_backorder()
                    elif hasattr(wizard, "process"):
                        wizard.process()

            if picking.state == "done":
                rec.state = "shipped"
            else:
                raise UserError(_(
                    "Picking is not validated yet.\n"
                    "Please open the Reservation Picking and complete the required step (availability/backorder)."
                ))

    def action_cancel(self):
        for rec in self:
            rec.state = "cancel"

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state != "cancel":
                raise UserError(_("Only cancelled shipments can be reset."))
            rec.state = "draft"

    def _build_container_so_line_description(self):
        self.ensure_one()
        prefix = self.env["ir.config_parameter"].sudo().get_param(
            "export_shipment.container_line_prefix", "Export Container"
        ) or "Export Container"
        parts = [
            prefix,
            _("Shipment No: %s") % (self.name or ""),
        ]
        if self.container_no:
            parts.append(_("Container: %s") % self.container_no)
        if self.seal_no:
            parts.append(_("Seal: %s") % self.seal_no)
        if self.destination:
            parts.append(_("Destination: %s") % self.destination)
        return "\n".join([p for p in parts if p])

    def action_update_sale_line_description(self):
        for rec in self:
            if not rec.sale_order_id:
                continue
            line = rec.sale_order_id.order_line[:1]
            if line:
                line.name = rec._build_container_so_line_description()


class ExportShipmentLine(models.Model):
    _name = "export.shipment.line"
    _description = "Export Shipment Line"
    _rec_name = "name"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Product", required=True)

    product_uom_id = fields.Many2one(
        "uom.uom",
        string="UoM",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )
    product_uom_qty = fields.Float(string="Quantity", required=True, default=1.0)

    name = fields.Char(string="Line", compute="_compute_name", store=True)

    @api.depends("shipment_id.name", "product_id.display_name", "product_uom_qty", "product_uom_id.name")
    def _compute_name(self):
        for rec in self:
            ship = rec.shipment_id.name or ""
            prod = rec.product_id.display_name or ""
            qty = rec.product_uom_qty or 0.0
            uom = rec.product_uom_id.name or ""
            rec.name = f"{ship} - {prod} ({qty:g} {uom})"

class ExportShipmentLotLine(models.Model):
    _name = "export.shipment.lot.line"
    _description = "Reserved Lot Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    line_id = fields.Many2one(
        "export.shipment.line",
        string="Related Line",
        required=True,
        ondelete="cascade",
        domain="[('shipment_id','=',shipment_id)]",
    )

    product_id = fields.Many2one(related="line_id.product_id", store=True, readonly=True)
    product_uom_id = fields.Many2one(related="line_id.product_uom_id", store=True, readonly=True)

    lot_id = fields.Many2one("stock.lot", string="Lot", required=True, domain="[('product_id', '=', product_id)]")
    qty = fields.Float(string="Reserved Qty", required=True, default=0.0)

    @api.onchange("line_id")
    def _onchange_line_id(self):
        for rec in self:
            if rec.line_id:
                rec.qty = rec.line_id.product_uom_qty

    @api.constrains("shipment_id", "line_id")
    def _check_line_same_shipment(self):
        for rec in self:
            if rec.line_id and rec.shipment_id and rec.line_id.shipment_id != rec.shipment_id:
                raise ValidationError(_("Related Line must belong to the same Shipment."))


class SaleOrder(models.Model):
    _inherit = "sale.order"

    export_shipment_id = fields.Many2one("export.shipment", string="Export Shipment", ondelete="set null")


class StockLot(models.Model):
    _inherit = "stock.lot"

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        auto = self.env["ir.config_parameter"].sudo().get_param("export_shipment.auto_lot", "0") == "1"
        if auto and "name" in fields_list and not res.get("name"):
            seq_code = self.env["ir.config_parameter"].sudo().get_param(
                "export_shipment.lot_sequence_code", "stock.lot.export"
            )
            res["name"] = self.env["ir.sequence"].next_by_code(seq_code) or res.get("name")
        return res

    @api.model_create_multi
    def create(self, vals_list):
        auto = self.env["ir.config_parameter"].sudo().get_param("export_shipment.auto_lot", "0") == "1"
        if auto:
            seq_code = self.env["ir.config_parameter"].sudo().get_param(
                "export_shipment.lot_sequence_code", "stock.lot.export"
            )
            for vals in vals_list:
                if vals.get("name") in (False, "New", _("New")):
                    vals["name"] = self.env["ir.sequence"].next_by_code(seq_code) or vals.get("name") or "New"
        return super().create(vals_list)


class ExportLogisticsSettings(models.Model):
    _name = "export.logistics.settings"
    _description = "Export & Logistics Settings"
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )

    container_product_tmpl_id = fields.Many2one(
        "product.template",
        string="Container Service Product",
        domain="[('type', '=', 'service')]",
        help="Service product used on Sale Orders (1 line per shipment).",
    )
    container_line_prefix = fields.Char(
        string="SO Line Prefix",
        default="Export Container",
        help="First line text used in the dynamic SO line description.",
    )
    auto_lot = fields.Boolean(
        string="Auto-generate Lot Numbers",
        help="If enabled, lots created from Inventory/MRP will get an automatic number from the configured sequence.",
        default=False,
    )

    @api.constrains("company_id")
    def _check_unique_company(self):
        for rec in self:
            if not rec.company_id:
                continue
            exists = self.search_count([("company_id", "=", rec.company_id.id), ("id", "!=", rec.id)])
            if exists:
                raise ValidationError(_("Settings already exist for this company."))

    def _sync_to_icp(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("export_shipment.container_product_tmpl_id", self.container_product_tmpl_id.id or 0)
        icp.set_param("export_shipment.container_line_prefix", self.container_line_prefix or "Export Container")
        icp.set_param("export_shipment.auto_lot", "1" if self.auto_lot else "0")
        icp.set_param("export_shipment.lot_sequence_code", "stock.lot.export")

    @api.model
    def create(self, vals):
        rec = super().create(vals)
        rec._sync_to_icp()
        return rec

    def write(self, vals):
        res = super().write(vals)
        for rec in self:
            rec._sync_to_icp()
        return res
