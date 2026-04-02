# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AgxFarm(models.Model):
    _name = "agx.farm"
    _description = "Farm"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"
    name = fields.Char(required=True, tracking=True)
    code = fields.Char(copy=False)
    partner_id = fields.Many2one("res.partner", string="Owner / Vendor", tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    region = fields.Char()
    location = fields.Char()
    capacity = fields.Float()
    active = fields.Boolean(default=True)
    note = fields.Html()


class AgxCrop(models.Model):
    _name = "agx.crop"
    _description = "Crop / Variety"
    _order = "name"
    name = fields.Char(required=True)
    code = fields.Char()
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxGrade(models.Model):
    _name = "agx.grade"
    _description = "Grade"
    _order = "sequence, name"
    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxSize(models.Model):
    _name = "agx.size"
    _description = "Size"
    _order = "sequence, name"
    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxDestination(models.Model):
    _name = "agx.destination"
    _description = "Destination"
    _order = "name"
    name = fields.Char(required=True)
    country_id = fields.Many2one("res.country")
    port_name = fields.Char()
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxShipmentCostType(models.Model):
    _name = "agx.shipment.cost.type"
    _description = "Shipment Cost Type"
    _order = "sequence, name"
    name = fields.Char(required=True)
    code = fields.Selection([("inland", "Inland Transport"), ("port", "Port Charges"), ("ocean", "Ocean Freight"), ("customs", "Customs Clearance"), ("other", "Other")], required=True, default="other")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    default_allocation_basis = fields.Selection([("qty", "By Quantity"), ("carton", "By Cartons"), ("net_weight", "By Net Weight"), ("gross_weight", "By Gross Weight"), ("equal", "Equal Share")], default="qty", required=True)
    note = fields.Text()


class AgxEvaluation(models.Model):
    _name = "agx.evaluation"
    _description = "Farm Evaluation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "evaluation_date desc, id desc"
    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    evaluation_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    partner_id = fields.Many2one("res.partner", string="Vendor", tracking=True)
    farm_id = fields.Many2one("agx.farm", required=True, tracking=True)
    crop_id = fields.Many2one("agx.crop", tracking=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)
    line_ids = fields.One2many("agx.evaluation.line", "evaluation_id", string="Evaluation Lines", copy=True)
    state = fields.Selection([("draft", "Draft"), ("approved", "Approved"), ("po_created", "PO Created"), ("closed", "Closed"), ("cancelled", "Cancelled")], default="draft", tracking=True)
    expected_total_qty = fields.Float(compute="_compute_totals", store=True)
    estimated_purchase_value = fields.Monetary(currency_field="currency_id", compute="_compute_totals", store=True)
    po_id = fields.Many2one("purchase.order", copy=False, readonly=True)
    note = fields.Html()
    @api.depends("line_ids.expected_qty", "line_ids.estimated_unit_price")
    def _compute_totals(self):
        for rec in self:
            rec.expected_total_qty = sum(rec.line_ids.mapped("expected_qty"))
            rec.estimated_purchase_value = sum(line.expected_qty * line.estimated_unit_price for line in rec.line_ids)
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.evaluation") or _("New")
        return super().create(vals_list)
    def action_approve(self):
        self.write({"state": "approved"})
    def action_reset_draft(self):
        self.write({"state": "draft"})
    def action_cancel(self):
        self.write({"state": "cancelled"})
    def action_create_purchase_order(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Please select a vendor before creating a purchase order."))
        if self.po_id:
            return self.action_view_purchase_order()
        po_vals = {"partner_id": self.partner_id.id, "company_id": self.company_id.id, "origin": self.name, "date_order": fields.Datetime.now(), "order_line": []}
        for line in self.line_ids:
            if not line.product_id or not line.expected_qty:
                continue
            po_vals["order_line"].append((0, 0, {"product_id": line.product_id.id, "name": line.product_id.display_name, "product_qty": line.expected_qty, "product_uom": line.uom_id.id or line.product_id.uom_po_id.id, "price_unit": line.estimated_unit_price, "date_planned": fields.Datetime.now()}))
        po = self.env["purchase.order"].create(po_vals)
        self.write({"po_id": po.id, "state": "po_created"})
        return self.action_view_purchase_order()
    def action_view_purchase_order(self):
        self.ensure_one()
        if not self.po_id:
            return False
        return {"type": "ir.actions.act_window", "name": _("Purchase Order"), "res_model": "purchase.order", "res_id": self.po_id.id, "view_mode": "form", "target": "current"}


class AgxEvaluationLine(models.Model):
    _name = "agx.evaluation.line"
    _description = "Farm Evaluation Line"
    _order = "sequence, id"
    sequence = fields.Integer(default=10)
    evaluation_id = fields.Many2one("agx.evaluation", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="evaluation_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="evaluation_id.currency_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    grade_id = fields.Many2one("agx.grade")
    size_id = fields.Many2one("agx.size")
    expected_ratio = fields.Float(string="Expected %")
    expected_qty = fields.Float()
    uom_id = fields.Many2one("uom.uom", string="UoM")
    estimated_unit_price = fields.Monetary(currency_field="currency_id")
    note = fields.Char()


class AgxRawReceipt(models.Model):
    _name = "agx.raw.receipt"
    _description = "Raw Receipt"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "receipt_date desc, id desc"
    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    receipt_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    partner_id = fields.Many2one("res.partner", string="Vendor")
    evaluation_id = fields.Many2one("agx.evaluation")
    purchase_order_id = fields.Many2one("purchase.order")
    picking_id = fields.Many2one("stock.picking", string="Stock Receipt")
    state = fields.Selection([("draft", "Draft"), ("received", "Received"), ("cancelled", "Cancelled")], default="draft", tracking=True)
    line_ids = fields.One2many("agx.raw.receipt.line", "receipt_id", string="Receipt Lines")
    total_qty = fields.Float(compute="_compute_total_qty", store=True)
    note = fields.Html()
    @api.depends("line_ids.qty")
    def _compute_total_qty(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("qty"))
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.raw.receipt") or _("New")
        return super().create(vals_list)
    def action_mark_received(self):
        self.write({"state": "received"})
    def action_cancel(self):
        self.write({"state": "cancelled"})


class AgxRawReceiptLine(models.Model):
    _name = "agx.raw.receipt.line"
    _description = "Raw Receipt Line"
    _order = "sequence, id"
    sequence = fields.Integer(default=10)
    receipt_id = fields.Many2one("agx.raw.receipt", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="receipt_id.company_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Raw Lot")
    farm_id = fields.Many2one("agx.farm")
    evaluation_id = fields.Many2one("agx.evaluation")
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom", string="UoM")
    note = fields.Char()


class AgxBatch(models.Model):
    _name = "agx.batch"
    _description = "Production Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "batch_date desc, id desc"
    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    batch_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    evaluation_id = fields.Many2one("agx.evaluation")
    raw_receipt_id = fields.Many2one("agx.raw.receipt")
    mrp_production_id = fields.Many2one("mrp.production", string="Manufacturing Order")
    state = fields.Selection([("draft", "Draft"), ("in_progress", "In Progress"), ("done", "Done"), ("cancelled", "Cancelled")], default="draft", tracking=True)
    input_line_ids = fields.One2many("agx.batch.input", "batch_id", string="Inputs", copy=True)
    output_line_ids = fields.One2many("agx.batch.output", "batch_id", string="Outputs", copy=True)
    input_qty = fields.Float(compute="_compute_qty_totals", store=True)
    output_qty = fields.Float(compute="_compute_qty_totals", store=True)
    variance_qty = fields.Float(compute="_compute_qty_totals", store=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)
    costing_status = fields.Selection([("manual", "Manual"), ("partial_actual", "Partial Actual"), ("full_actual", "Full Actual")], compute="_compute_costing_totals", store=True)
    manual_raw_material_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    manual_operation_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    manual_other_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    actual_raw_material_cost = fields.Monetary(currency_field="currency_id", default=0.0, readonly=True)
    actual_operation_cost = fields.Monetary(currency_field="currency_id", default=0.0, readonly=True)
    actual_other_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    effective_allocable_cost = fields.Monetary(currency_field="currency_id", compute="_compute_costing_totals", store=True)
    total_relative_sales_value = fields.Monetary(currency_field="currency_id", compute="_compute_costing_totals", store=True)
    actual_cost_last_refresh = fields.Datetime(readonly=True)
    @api.depends("input_line_ids.qty", "output_line_ids.qty")
    def _compute_qty_totals(self):
        for rec in self:
            rec.input_qty = sum(rec.input_line_ids.mapped("qty"))
            rec.output_qty = sum(rec.output_line_ids.mapped("qty"))
            rec.variance_qty = rec.input_qty - rec.output_qty
    @api.depends("manual_raw_material_cost", "manual_operation_cost", "manual_other_cost", "actual_raw_material_cost", "actual_operation_cost", "actual_other_cost", "output_line_ids.sales_value")
    def _compute_costing_totals(self):
        for rec in self:
            raw_cost = rec.actual_raw_material_cost or rec.manual_raw_material_cost
            op_cost = rec.actual_operation_cost or rec.manual_operation_cost
            other_cost = rec.actual_other_cost or rec.manual_other_cost
            rec.effective_allocable_cost = raw_cost + op_cost + other_cost
            rec.total_relative_sales_value = sum(rec.output_line_ids.mapped("sales_value"))
            has_actual_raw = bool(rec.actual_raw_material_cost)
            has_actual_op = bool(rec.actual_operation_cost)
            rec.costing_status = "full_actual" if has_actual_raw and has_actual_op else ("partial_actual" if has_actual_raw or has_actual_op else "manual")
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.batch") or _("New")
        return super().create(vals_list)
    def action_start(self):
        self.write({"state": "in_progress"})
    def action_done(self):
        self.write({"state": "done"})
    def action_cancel(self):
        self.write({"state": "cancelled"})
    def action_refresh_actual_costs(self):
        for rec in self:
            raw_cost = 0.0
            op_cost = 0.0
            if rec.mrp_production_id:
                raw_moves = rec.mrp_production_id.move_raw_ids.filtered(lambda m: m.state == "done")
                for move in raw_moves:
                    price = getattr(move, "price_unit", 0.0) or move.product_id.standard_price
                    raw_cost += move.quantity * price
                for wo in rec.mrp_production_id.workorder_ids:
                    duration_hours = (wo.duration or 0.0) / 60.0
                    wc_cost = getattr(wo.workcenter_id, "costs_hour", 0.0)
                    op_cost += duration_hours * wc_cost
            rec.write({"actual_raw_material_cost": raw_cost, "actual_operation_cost": op_cost, "actual_cost_last_refresh": fields.Datetime.now()})


class AgxBatchInput(models.Model):
    _name = "agx.batch.input"
    _description = "Batch Input"
    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    raw_receipt_line_id = fields.Many2one("agx.raw.receipt.line")
    lot_id = fields.Many2one("stock.lot")
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    note = fields.Char()


class AgxBatchOutput(models.Model):
    _name = "agx.batch.output"
    _description = "Batch Output"
    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="batch_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="batch_id.currency_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Output Lot")
    grade_id = fields.Many2one("agx.grade")
    size_id = fields.Many2one("agx.size")
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    sales_price_unit = fields.Monetary(currency_field="currency_id", default=0.0)
    sales_value = fields.Monetary(currency_field="currency_id", compute="_compute_cost_share", store=True)
    relative_sales_ratio = fields.Float(compute="_compute_cost_share", store=True, digits=(16, 6))
    allocated_cost = fields.Monetary(currency_field="currency_id", compute="_compute_cost_share", store=True)
    cost_per_unit = fields.Monetary(currency_field="currency_id", compute="_compute_cost_share", store=True)
    @api.depends("qty", "sales_price_unit", "batch_id.effective_allocable_cost", "batch_id.total_relative_sales_value")
    def _compute_cost_share(self):
        for rec in self:
            rec.sales_value = (rec.qty or 0.0) * (rec.sales_price_unit or 0.0)
            total_sales = rec.batch_id.total_relative_sales_value or 0.0
            rec.relative_sales_ratio = (rec.sales_value / total_sales) if total_sales else 0.0
            rec.allocated_cost = (rec.batch_id.effective_allocable_cost or 0.0) * rec.relative_sales_ratio
            rec.cost_per_unit = (rec.allocated_cost / rec.qty) if rec.qty else 0.0


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
    manual_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    actual_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    total_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    cost_per_container = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    revenue_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_profit_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_margin_pct = fields.Float(compute="_compute_profitability", store=True, digits=(16, 2))
    costing_status = fields.Selection([("manual", "Manual"), ("partial_actual", "Partial Actual"), ("full_actual", "Full Actual")], compute="_compute_profitability", store=True)
    note = fields.Html()
    @api.depends("line_ids.product_qty", "lot_line_ids.reserved_qty", "cost_line_ids.cost_source", "cost_line_ids.effective_amount", "sale_order_id.amount_untaxed", "container_count")
    def _compute_profitability(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_qty"))
            rec.total_reserved_qty = sum(rec.lot_line_ids.mapped("reserved_qty"))
            manual_total = sum(rec.cost_line_ids.filtered(lambda l: l.cost_source == "manual").mapped("effective_amount"))
            actual_total = sum(rec.cost_line_ids.filtered(lambda l: l.cost_source != "manual").mapped("effective_amount"))
            rec.manual_logistics_cost = manual_total
            rec.actual_logistics_cost = actual_total
            rec.total_logistics_cost = manual_total + actual_total
            rec.cost_per_container = rec.total_logistics_cost / rec.container_count if rec.container_count else 0.0
            rec.revenue_amount = rec.sale_order_id.amount_untaxed if rec.sale_order_id else 0.0
            rec.gross_profit_amount = rec.revenue_amount - rec.total_logistics_cost
            rec.gross_margin_pct = (rec.gross_profit_amount / rec.revenue_amount * 100.0) if rec.revenue_amount else 0.0
            has_manual = bool(manual_total)
            has_actual = bool(actual_total)
            rec.costing_status = "partial_actual" if has_manual and has_actual else ("full_actual" if has_actual else "manual")
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.shipment") or _("New")
        return super().create(vals_list)
    def action_reserve(self): self.write({"state": "reserved"})
    def action_ship(self): self.write({"state": "shipped"})
    def action_cancel(self): self.write({"state": "cancelled"})
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
            "order_line": [(0, 0, {"product_id": product.id, "name": f"{self.company_id.agx_so_line_prefix or 'Shipment'} {self.name}", "product_uom_qty": self.container_count or 1, "price_unit": 0.0})],
        })
        self.sale_order_id = so.id
        return self.action_view_sale_order()
    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {"type": "ir.actions.act_window", "name": _("Sales Order"), "res_model": "sale.order", "res_id": self.sale_order_id.id, "view_mode": "form", "target": "current"}


class AgxShipmentLine(models.Model):
    _name = "agx.shipment.line"
    _description = "Shipment Product Line"
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
    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="shipment_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="shipment_id.currency_id", store=True, readonly=True)
    name = fields.Char(required=True)
    cost_type_id = fields.Many2one("agx.shipment.cost.type", required=True)
    allocation_basis = fields.Selection([("qty", "By Quantity"), ("carton", "By Cartons"), ("net_weight", "By Net Weight"), ("gross_weight", "By Gross Weight"), ("equal", "Equal Share")], default="qty", required=True)
    cost_source = fields.Selection([("manual", "Manual"), ("vendor_bill", "Vendor Bill"), ("vendor_bill_line", "Vendor Bill Line"), ("landed_cost", "Landed Cost")], default="manual", required=True)
    manual_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    source_amount = fields.Monetary(currency_field="currency_id", compute="_compute_source_amount", store=True)
    effective_amount = fields.Monetary(currency_field="currency_id", compute="_compute_source_amount", store=True)
    vendor_bill_id = fields.Many2one("account.move", domain="[('move_type', '=', 'in_invoice')]")
    vendor_bill_line_id = fields.Many2one("account.move.line", domain="[('move_id', '=', vendor_bill_id)]")
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


class ResCompany(models.Model):
    _inherit = "res.company"
    agx_container_service_product_id = fields.Many2one("product.product", string="Container Service Product")
    agx_so_line_prefix = fields.Char(default="Shipment")
    agx_auto_generate_lot_numbers = fields.Boolean(default=True)
    agx_default_logistics_basis = fields.Selection([("qty", "By Quantity"), ("carton", "By Cartons"), ("net_weight", "By Net Weight"), ("gross_weight", "By Gross Weight"), ("equal", "Equal Share")], default="qty")
    agx_allow_vendor_bill_cost_source = fields.Boolean(default=True)
    agx_allow_landed_cost_source = fields.Boolean(default=True)
    agx_margin_precision = fields.Integer(default=2)
    agx_strict_lot_traceability = fields.Boolean(default=True)
    agx_dashboard_show_destination_analysis = fields.Boolean(default=True)
    agx_dashboard_show_customer_analysis = fields.Boolean(default=True)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"
    agx_container_service_product_id = fields.Many2one(related="company_id.agx_container_service_product_id", readonly=False)
    agx_so_line_prefix = fields.Char(related="company_id.agx_so_line_prefix", readonly=False)
    agx_auto_generate_lot_numbers = fields.Boolean(related="company_id.agx_auto_generate_lot_numbers", readonly=False)
    agx_default_logistics_basis = fields.Selection(related="company_id.agx_default_logistics_basis", readonly=False)
    agx_allow_vendor_bill_cost_source = fields.Boolean(related="company_id.agx_allow_vendor_bill_cost_source", readonly=False)
    agx_allow_landed_cost_source = fields.Boolean(related="company_id.agx_allow_landed_cost_source", readonly=False)
    agx_margin_precision = fields.Integer(related="company_id.agx_margin_precision", readonly=False)
    agx_strict_lot_traceability = fields.Boolean(related="company_id.agx_strict_lot_traceability", readonly=False)
    agx_dashboard_show_destination_analysis = fields.Boolean(related="company_id.agx_dashboard_show_destination_analysis", readonly=False)
    agx_dashboard_show_customer_analysis = fields.Boolean(related="company_id.agx_dashboard_show_customer_analysis", readonly=False)
