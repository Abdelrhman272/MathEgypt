from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AgxShipment(models.Model):
    _name = "agx.shipment"
    _description = "Export Shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "shipment_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    shipment_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)
    customer_id = fields.Many2one("res.partner", string="Customer", tracking=True)
    destination_id = fields.Many2one("agx.destination", tracking=True)
    container_no = fields.Char(tracking=True)
    container_count = fields.Integer(default=1, tracking=True)
    seal_no = fields.Char()
    state = fields.Selection([("draft", "Draft"), ("reserved", "Reserved"), ("shipped", "Shipped"), ("cancelled", "Cancelled")], default="draft", tracking=True)
    line_ids = fields.One2many("agx.shipment.line", "shipment_id", string="Products", copy=True)
    lot_line_ids = fields.One2many("agx.shipment.lot.line", "shipment_id", string="Reserved Lots", copy=True)
    cost_line_ids = fields.One2many("agx.shipment.cost.line", "shipment_id", string="Logistics Costs", copy=True)
    sale_order_id = fields.Many2one("sale.order", copy=False)
    total_qty = fields.Float(compute="_compute_profitability", store=True)
    total_reserved_qty = fields.Float(compute="_compute_profitability", store=True)
    total_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    cost_per_container = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    revenue_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_profit_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_margin_pct = fields.Float(compute="_compute_profitability", store=True, digits=(16, 2))
    note = fields.Html()

    @api.depends("line_ids.product_qty", "lot_line_ids.reserved_qty", "cost_line_ids.effective_amount", "sale_order_id.amount_untaxed", "container_count")
    def _compute_profitability(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_qty"))
            rec.total_reserved_qty = sum(rec.lot_line_ids.mapped("reserved_qty"))
            rec.total_logistics_cost = sum(rec.cost_line_ids.mapped("effective_amount"))
            rec.cost_per_container = rec.total_logistics_cost / rec.container_count if rec.container_count else 0.0
            rec.revenue_amount = rec.sale_order_id.amount_untaxed if rec.sale_order_id else 0.0
            rec.gross_profit_amount = rec.revenue_amount - rec.total_logistics_cost
            rec.gross_margin_pct = (rec.gross_profit_amount / rec.revenue_amount * 100.0) if rec.revenue_amount else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.shipment") or _("New")
        return super().create(vals_list)

    def action_reserve(self):
        self.write({"state": "reserved"})

    def action_ship(self):
        self.write({"state": "shipped"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_create_sale_order(self):
        self.ensure_one()
        if not self.customer_id:
            raise UserError(_("Please select a customer before creating the Sales Order."))
        if self.sale_order_id:
            return self.action_view_sale_order()
        product = self.company_id.agx_container_service_product_id or self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        if not product:
            raise UserError(_("Please configure a container service product in Settings."))
        so = self.env["sale.order"].create({
            "partner_id": self.customer_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
            "order_line": [(0, 0, {
                "product_id": product.id,
                "name": f"{self.company_id.agx_so_line_prefix or 'Shipment'} {self.name}",
                "product_uom_qty": self.container_count or 1,
                "price_unit": 0.0,
            })],
        })
        self.sale_order_id = so.id
        return self.action_view_sale_order()

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Sales Order"),
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }


class AgxShipmentLine(models.Model):
    _name = "agx.shipment.line"
    _description = "Shipment Product Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="shipment_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="shipment_id.currency_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    grade_id = fields.Many2one("agx.grade")
    size_id = fields.Many2one("agx.size")
    product_qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    carton_qty = fields.Float(default=0.0)
    net_weight = fields.Float(default=0.0)
    gross_weight = fields.Float(default=0.0)
    allocated_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_allocated_costs", store=True)
    cost_per_carton = fields.Monetary(currency_field="currency_id", compute="_compute_allocated_costs", store=True)
    cost_per_qty = fields.Monetary(currency_field="currency_id", compute="_compute_allocated_costs", store=True)

    @api.depends("shipment_id.cost_line_ids.effective_amount", "shipment_id.cost_line_ids.allocation_basis", "shipment_id.line_ids.product_qty", "shipment_id.line_ids.carton_qty", "shipment_id.line_ids.net_weight", "shipment_id.line_ids.gross_weight")
    def _compute_allocated_costs(self):
        for line in self:
            total_allocated = 0.0
            shipment = line.shipment_id
            lines = shipment.line_ids
            for cost_line in shipment.cost_line_ids:
                if cost_line.allocation_basis == "carton":
                    denominator = sum(lines.mapped("carton_qty")); numerator = line.carton_qty
                elif cost_line.allocation_basis == "net_weight":
                    denominator = sum(lines.mapped("net_weight")); numerator = line.net_weight
                elif cost_line.allocation_basis == "gross_weight":
                    denominator = sum(lines.mapped("gross_weight")); numerator = line.gross_weight
                elif cost_line.allocation_basis == "equal":
                    denominator = len(lines); numerator = 1.0 if line.id else 0.0
                else:
                    denominator = sum(lines.mapped("product_qty")); numerator = line.product_qty
                if denominator:
                    total_allocated += cost_line.effective_amount * (numerator / denominator)
            line.allocated_logistics_cost = total_allocated
            line.cost_per_carton = total_allocated / line.carton_qty if line.carton_qty else 0.0
            line.cost_per_qty = total_allocated / line.product_qty if line.product_qty else 0.0


class AgxShipmentLotLine(models.Model):
    _name = "agx.shipment.lot.line"
    _description = "Reserved Lot Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    shipment_line_id = fields.Many2one("agx.shipment.line")
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", required=True)
    batch_output_id = fields.Many2one("agx.batch.output")
    available_qty = fields.Float()
    reserved_qty = fields.Float(required=True, default=1.0)
    note = fields.Char()

    @api.constrains("reserved_qty", "available_qty")
    def _check_reserved_qty(self):
        for rec in self:
            if rec.available_qty and rec.reserved_qty > rec.available_qty:
                raise ValidationError(_("Reserved quantity cannot exceed available quantity."))


class AgxShipmentCostLine(models.Model):
    _name = "agx.shipment.cost.line"
    _description = "Shipment Cost Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="shipment_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="shipment_id.currency_id", store=True, readonly=True)
    name = fields.Char(required=True)
    cost_type_id = fields.Many2one("agx.shipment.cost.type", required=True)
    allocation_basis = fields.Selection([
        ("qty", "By Quantity"),
        ("carton", "By Cartons"),
        ("net_weight", "By Net Weight"),
        ("gross_weight", "By Gross Weight"),
        ("equal", "Equal Share"),
    ], default="qty", required=True)
    cost_source = fields.Selection([
        ("manual", "Manual"),
        ("vendor_bill", "Vendor Bill"),
        ("vendor_bill_line", "Vendor Bill Line"),
        ("landed_cost", "Landed Cost"),
    ], default="manual", required=True)
    manual_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    source_amount = fields.Monetary(currency_field="currency_id", compute="_compute_source_amount", store=True)
    effective_amount = fields.Monetary(currency_field="currency_id", compute="_compute_source_amount", store=True)
    vendor_bill_id = fields.Many2one("account.move", domain="[('move_type', '=', 'in_invoice')]")
    vendor_bill_line_id = fields.Many2one("account.move.line")
    landed_cost_id = fields.Many2one("stock.landed.cost")
    note = fields.Char()

    @api.depends("cost_source", "manual_amount", "vendor_bill_id.amount_untaxed", "vendor_bill_line_id.price_subtotal", "landed_cost_id.amount_total")
    def _compute_source_amount(self):
        for rec in self:
            source_amount = 0.0
            if rec.cost_source == "vendor_bill_line" and rec.vendor_bill_line_id:
                source_amount = rec.vendor_bill_line_id.price_subtotal
            elif rec.cost_source == "vendor_bill" and rec.vendor_bill_id:
                source_amount = rec.vendor_bill_id.amount_untaxed
            elif rec.cost_source == "landed_cost" and rec.landed_cost_id:
                source_amount = rec.landed_cost_id.amount_total
            rec.source_amount = source_amount
            rec.effective_amount = source_amount if rec.cost_source != "manual" else rec.manual_amount
