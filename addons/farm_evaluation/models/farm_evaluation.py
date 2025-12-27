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
        domain=[('purchase_ok', '=', True), ('product_tmpl_id.detailed_type', '=', 'product')]
        tracking=True,
    )

    total_expected_qty = fields.Float(string="Total Expected Qty", required=True, tracking=True)
    uom_id = fields.Many2one(related="raw_product_id.uom_id", readonly=True)

    line_ids = fields.One2many("farm.evaluation.line", "evaluation_id", string="Grades")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("po_created", "PO Created"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    purchase_order_id = fields.Many2one("purchase.order", readonly=True, copy=False)

    @api.model
    def create(self, vals):
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
        """
        Create RFQ/PO from evaluation:
        - One PO line per grade (same raw product), qty = expected_qty
        - Link PO back to evaluation
        """
        for rec in self:
            if rec.state != "approved":
                raise UserError(_("Evaluation must be Approved first."))
            if rec.purchase_order_id:
                raise UserError(_("A Purchase Order is already created for this evaluation."))

            # basic validations
            if rec.total_expected_qty <= 0:
                raise UserError(_("Total Expected Qty must be greater than zero."))

            po_vals = {
                "partner_id": rec.vendor_id.id,
                "origin": rec.name,
                "farm_evaluation_id": rec.id,
            }
            po = self.env["purchase.order"].create(po_vals)

            lines_vals = []
            for line in rec.line_ids:
                if line.expected_qty <= 0:
                    continue
                # create one purchase line per grade
                lines_vals.append((0, 0, {
                    "order_id": po.id,
                    "product_id": rec.raw_product_id.id,
                    "name": f"{rec.raw_product_id.display_name} - Grade {line.grade}",
                    "product_qty": line.expected_qty,
                    "product_uom": rec.uom_id.id,
                    "farm_grade": line.grade,
                    "farm_evaluation_line_id": line.id,
                }))

            if not lines_vals:
                raise UserError(_("All grade lines have zero expected qty."))

            po.write({"order_line": lines_vals})

            rec.purchase_order_id = po.id
            rec.state = "po_created"

        return {
            "type": "ir.actions.act_window",
            "name": _("Request for Quotation"),
            "res_model": "purchase.order",
            "view_mode": "form",
            "res_id": self.purchase_order_id.id,
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
