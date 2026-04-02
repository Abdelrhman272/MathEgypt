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
    line_ids = fields.One2many("agx.evaluation.line", "evaluation_id", string="Evaluation Lines", copy=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("approved", "Approved"),
        ("po_created", "PO Created"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ], default="draft", tracking=True)
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
        po_vals = {
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
            "date_order": fields.Datetime.now(),
            "order_line": [],
        }
        for line in self.line_ids:
            if not line.product_id or not line.expected_qty:
                continue
            po_vals["order_line"].append((0, 0, {
                "product_id": line.product_id.id,
                "name": line.product_id.display_name,
                "product_qty": line.expected_qty,
                "product_uom": line.uom_id.id or line.product_id.uom_po_id.id,
                "price_unit": line.estimated_unit_price,
                "date_planned": fields.Datetime.now(),
            }))
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
