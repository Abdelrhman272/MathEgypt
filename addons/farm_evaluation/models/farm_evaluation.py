from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FarmEvaluation(models.Model):
    _name = "farm.evaluation"
    _description = "Farm Evaluation"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(default="New", required=True, copy=False, readonly=True)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)

    vendor_id = fields.Many2one(
        "res.partner",
        required=True,
        tracking=True,
        domain=[("supplier_rank", ">", 0)],
    )

    raw_product_id = fields.Many2one(
        "product.product",
        string="Raw Material",
        required=True,
        tracking=True,
        # IMPORTANT: keep domain safe + multi-company friendly
        domain=lambda self: [
            ("purchase_ok", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ],
    )

    uom_id = fields.Many2one(related="raw_product_id.uom_id", readonly=True)
    total_expected_qty = fields.Float(string="Total Expected Qty", required=True, tracking=True)

    season = fields.Char(string="")(string="Season", tracking=True)

    line_ids = fields.One2many(
        "farm.evaluation.line",
        "evaluation_id",
        string="Expected Grades",
        copy=True,
    )

    purchase_order_id = fields.Many2one("purchase.order", readonly=True, copy=False)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("po_created", "PO Created"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        required=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("farm.evaluation") or _("New")
        return super().create(vals_list)

    def action_approve(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("Please add at least one grade line."))
            rec.state = "approved"

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_set_draft(self):
        self.write({"state": "draft"})

    def action_create_po(self):
        """Create a single RFQ/PO line for the raw material (total weight).

        Business rule (per client):
        - Evaluation has ONE raw product + total expected qty (weight)
        - Grade lines are ONLY for expected distribution comparison, NOT for purchasing lines
        """
        self.ensure_one()

        if self.state != "approved":
            raise UserError(_("Evaluation must be Approved first."))
        if self.purchase_order_id:
            raise UserError(_("A Purchase Order is already created for this evaluation."))

        if not self.vendor_id:
            raise UserError(_("Please select a Vendor."))
        if not self.raw_product_id:
            raise UserError(_("Please select a Raw Material product."))
        if self.total_expected_qty <= 0:
            raise UserError(_("Total Expected Qty must be greater than zero."))

        po_vals = {
            "partner_id": self.vendor_id.id,
            "origin": self.name,
            "farm_evaluation_id": self.id,  # requires purchase.order extension field
        }
        po = self.env["purchase.order"].create(po_vals)

        # ONE purchase line only (raw material by total weight)
        line_vals = (0, 0, {
            "product_id": self.raw_product_id.id,
            "name": self.raw_product_id.display_name,
            "product_qty": self.total_expected_qty,
            "product_uom_id": self.raw_product_id.uom_id.id,
        })
        po.write({"order_line": [line_vals]})

        self.purchase_order_id = po
        self.state = "po_created"

        return {
            "type": "ir.actions.act_window",
            "name": _("Request for Quotation"),
            "res_model": "purchase.order",
            "view_mode": "form",
            "res_id": po.id,
        }


class FarmEvaluationLine(models.Model):
    _name = "farm.evaluation.line"
    _description = "Farm Evaluation Grade Line"
    _order = "id asc"

    evaluation_id = fields.Many2one("farm.evaluation", required=True, ondelete="cascade")
    grade = fields.Selection(
        [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")],
        required=True,
        default="A",
    )

    expected_percent = fields.Float(string="Expected %", required=True)
    expected_qty = fields.Float(string="Expected Qty", compute="_compute_expected_qty", store=True)
    uom_id = fields.Many2one(related="evaluation_id.uom_id", readonly=True)

    @api.depends("expected_percent", "evaluation_id.total_expected_qty")
    def _compute_expected_qty(self):
        for rec in self:
            total = rec.evaluation_id.total_expected_qty or 0.0
            rec.expected_qty = (total * (rec.expected_percent or 0.0)) / 100.0
