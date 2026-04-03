from odoo import _, api, fields, models


class AgxBatch(models.Model):
    _name = "agx.batch"
    _description = "Production Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "batch_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    batch_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    evaluation_id = fields.Many2one("agx.evaluation")
    incoming_picking_id = fields.Many2one("stock.picking", string="Incoming Receipt")
    mrp_production_id = fields.Many2one("mrp.production", string="Manufacturing Order")
    state = fields.Selection([
        ("draft", "Draft"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ], default="draft", tracking=True)
    input_line_ids = fields.One2many("agx.batch.input", "batch_id", string="Inputs", copy=True)
    output_line_ids = fields.One2many("agx.batch.output", "batch_id", string="Outputs", copy=True)
    input_qty = fields.Float(compute="_compute_qty_totals", store=True)
    output_qty = fields.Float(compute="_compute_qty_totals", store=True)
    variance_qty = fields.Float(compute="_compute_qty_totals", store=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)

    costing_status = fields.Selection([
        ("manual", "Manual"),
        ("partial_actual", "Partial Actual"),
        ("full_actual", "Full Actual"),
    ], compute="_compute_costing_totals", store=True)
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

    @api.depends(
        "manual_raw_material_cost",
        "manual_operation_cost",
        "manual_other_cost",
        "actual_raw_material_cost",
        "actual_operation_cost",
        "actual_other_cost",
        "output_line_ids.sales_value",
    )
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
                    qty = getattr(move, "quantity", 0.0) or getattr(move, "quantity_done", 0.0)
                    price = getattr(move, "price_unit", 0.0) or move.product_id.standard_price
                    raw_cost += qty * price
                for wo in rec.mrp_production_id.workorder_ids:
                    duration_hours = (wo.duration or 0.0) / 60.0
                    wc_cost = getattr(wo.workcenter_id, "costs_hour", 0.0)
                    op_cost += duration_hours * wc_cost
            else:
                raw_cost = sum((line.qty or 0.0) * (line.product_id.standard_price or 0.0) for line in rec.input_line_ids)
            rec.write({
                "actual_raw_material_cost": raw_cost,
                "actual_operation_cost": op_cost,
                "actual_cost_last_refresh": fields.Datetime.now(),
            })


class AgxBatchInput(models.Model):
    _name = "agx.batch.input"
    _description = "Batch Input"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot")
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    note = fields.Char()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id


class AgxBatchOutput(models.Model):
    _name = "agx.batch.output"
    _description = "Batch Output"
    _order = "sequence, id"

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

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id

    @api.depends("qty", "sales_price_unit", "batch_id.effective_allocable_cost", "batch_id.total_relative_sales_value")
    def _compute_cost_share(self):
        for rec in self:
            rec.sales_value = (rec.qty or 0.0) * (rec.sales_price_unit or 0.0)
            total_sales = rec.batch_id.total_relative_sales_value or 0.0
            rec.relative_sales_ratio = (rec.sales_value / total_sales) if total_sales else 0.0
            rec.allocated_cost = (rec.batch_id.effective_allocable_cost or 0.0) * rec.relative_sales_ratio
            rec.cost_per_unit = (rec.allocated_cost / rec.qty) if rec.qty else 0.0
