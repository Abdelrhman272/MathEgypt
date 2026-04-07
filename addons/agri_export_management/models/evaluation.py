from odoo import _, api, fields, models
from odoo.exceptions import UserError


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
    farm_expected_qty = fields.Float(string="Expected Purchase Qty", tracking=True)
    line_ids = fields.One2many("agx.evaluation.line", "evaluation_id", string="Evaluation Lines", copy=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("approved", "Approved"),
        ("po_created", "PO Created"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ], default="draft", tracking=True)
    expected_total_qty = fields.Float(compute="_compute_totals")
    actual_total_qty = fields.Float(compute="_compute_totals")
    variance_qty = fields.Float(compute="_compute_totals")
    achievement_pct = fields.Float(compute="_compute_totals", digits=(16, 2))
    estimated_purchase_value = fields.Monetary(currency_field="currency_id", compute="_compute_totals")
    po_id = fields.Many2one("purchase.order", copy=False, readonly=True)
    po_count = fields.Integer(compute="_compute_links")
    receipt_count = fields.Integer(compute="_compute_links")
    batch_count = fields.Integer(compute="_compute_links")
    note = fields.Html()

    @api.depends(
        "farm_expected_qty",
        "line_ids.expected_qty",
        "line_ids.estimated_unit_price",
        "line_ids.actual_qty",
        "line_ids.variance_qty",
    )
    def _compute_totals(self):
        for rec in self:
            rec.expected_total_qty = sum(rec.line_ids.mapped("expected_qty"))
            rec.actual_total_qty = sum(rec.line_ids.mapped("actual_qty"))
            rec.variance_qty = rec.actual_total_qty - rec.expected_total_qty
            rec.achievement_pct = (rec.actual_total_qty / rec.expected_total_qty * 100.0) if rec.expected_total_qty else 0.0
            rec.estimated_purchase_value = sum(line.expected_qty * line.estimated_unit_price for line in rec.line_ids)

    def _compute_links(self):
        Purchase = self.env["purchase.order"]
        Picking = self.env["stock.picking"]
        Batch = self.env["agx.batch"]
        for rec in self:
            rec.po_count = Purchase.search_count([("agx_evaluation_id", "=", rec.id)])
            rec.receipt_count = Picking.search_count([("agx_evaluation_id", "=", rec.id), ("picking_type_id.code", "=", "incoming")])
            rec.batch_count = Batch.search_count([("evaluation_id", "=", rec.id)])

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
        # Do not create or link legacy farm.evaluation records from older modules.
        # agri_export_management must stay self-contained and use agx_evaluation_id only.
        order_lines = []
        for line in self.line_ids:
            if not line.product_id or not line.expected_qty:
                continue
            order_lines.append((0, 0, {
                "product_id": line.product_id.id,
                "name": line.product_id.display_name,
                "product_qty": line.expected_qty,
                "product_uom_id": (line.uom_id or line.product_id.uom_id).id,
                "price_unit": line.estimated_unit_price,
                "date_planned": fields.Datetime.now(),
            }))
        if not order_lines:
            raise UserError(_("Please add at least one evaluation line with product and quantity before creating a purchase order."))
        po_vals = {
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
            "date_order": fields.Datetime.now(),
            "agx_evaluation_id": self.id,
            "order_line": order_lines,
        }
        po = self.env["purchase.order"].create(po_vals)
        self.write({"po_id": po.id, "state": "po_created"})
        return self.action_view_purchase_order()

    def action_view_purchase_order(self):
        self.ensure_one()
        if not self.po_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Purchase Order"),
            "res_model": "purchase.order",
            "res_id": self.po_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_receipts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Incoming Receipts"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("agx_evaluation_id", "=", self.id), ("picking_type_id.code", "=", "incoming")],
            "target": "current",
        }

    def action_view_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Production Batches"),
            "res_model": "agx.batch",
            "view_mode": "list,form",
            "domain": [("evaluation_id", "=", self.id)],
            "target": "current",
        }


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
    expected_qty = fields.Float(compute="_compute_expected_qty", store=True)
    actual_qty = fields.Float(compute="_compute_actuals")
    variance_qty = fields.Float(compute="_compute_actuals")
    achievement_pct = fields.Float(compute="_compute_actuals", digits=(16, 2))
    uom_id = fields.Many2one("uom.uom", string="UoM")
    estimated_unit_price = fields.Monetary(currency_field="currency_id")
    note = fields.Char()


    @api.depends("expected_ratio", "evaluation_id.farm_expected_qty")
    def _compute_expected_qty(self):
        for rec in self:
            rec.expected_qty = (rec.evaluation_id.farm_expected_qty or 0.0) * ((rec.expected_ratio or 0.0) / 100.0)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id

    @api.depends("evaluation_id", "product_id", "grade_id", "size_id")
    def _compute_actuals(self):
        BatchOutput = self.env["agx.batch.output"]
        for rec in self:
            domain = [("batch_id.evaluation_id", "=", rec.evaluation_id.id), ("product_id", "=", rec.product_id.id)]
            if rec.grade_id:
                domain.append(("grade_id", "=", rec.grade_id.id))
            if rec.size_id:
                domain.append(("size_id", "=", rec.size_id.id))
            outputs = BatchOutput.search(domain)
            rec.actual_qty = sum(outputs.mapped("qty"))
            rec.variance_qty = rec.actual_qty - rec.expected_qty
            rec.achievement_pct = (rec.actual_qty / rec.expected_qty * 100.0) if rec.expected_qty else 0.0
