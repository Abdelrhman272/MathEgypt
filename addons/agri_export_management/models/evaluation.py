# -*- coding: utf-8 -*-
"""
evaluation.py — Farm Evaluation & Evaluation Lines
====================================================
An evaluation is the starting point of every purchase cycle.
Before buying, the operations team visits the farm, estimates the expected
yield per grade/size combination, and records that information here.

Flow
----
  Draft → Approve → Create PO → (receive goods) → Close

Key relations
-------------
  agx.evaluation          1──M  agx.evaluation.line   (grade/size breakdown)
  agx.evaluation          1──1  purchase.order        (via po_id)
  agx.evaluation          M──1  agx.season            (analytic grouping)
  agx.evaluation.line     M──M  agx.batch.output      (actual vs expected)

Actual qty on each evaluation line is computed dynamically from all
``agx.batch.output`` records whose batch is linked to this evaluation,
filtered by the same product/grade/size combination.  This gives the
operations team a real-time achievement percentage without any manual
data entry.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AgxEvaluation(models.Model):
    """Farm evaluation — pre-purchase yield assessment."""

    _name = "agx.evaluation"
    _description = "Farm Evaluation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "evaluation_date desc, id desc"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(
        default=lambda self: _("New"),
        copy=False,
        readonly=True,
        help="Auto-generated reference from the AGX Evaluation sequence.",
    )
    evaluation_date = fields.Date(
        default=fields.Date.context_today, tracking=True
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Core references
    # ------------------------------------------------------------------
    farm_id = fields.Many2one(
        "agx.farm",
        required=True,
        tracking=True,
        help="Farm where the produce will be purchased.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        tracking=True,
        help="Defaults from the farm owner; can be overridden.",
    )
    crop_id = fields.Many2one(
        "agx.crop",
        tracking=True,
        help="Crop type being evaluated.",
    )
    season_id = fields.Many2one(
        "agx.season",
        string="Season",
        tracking=True,
        index=True,
        help=(
            "Production season this evaluation belongs to. "
            "All costs post to the season's analytic account."
        ),
    )

    # ------------------------------------------------------------------
    # Quantity estimates
    # ------------------------------------------------------------------
    farm_expected_qty = fields.Float(
        string="Expected Purchase Qty",
        tracking=True,
        help="Total expected quantity from the farm (in UoM of the crop).",
    )

    # ------------------------------------------------------------------
    # Grade / size breakdown lines
    # ------------------------------------------------------------------
    line_ids = fields.One2many(
        "agx.evaluation.line",
        "evaluation_id",
        string="Grades & Sizes",
        copy=True,
    )

    # ------------------------------------------------------------------
    # Computed totals
    # ------------------------------------------------------------------
    expected_total_qty = fields.Float(compute="_compute_totals", store=False)
    actual_total_qty = fields.Float(compute="_compute_totals", store=False)
    variance_qty = fields.Float(compute="_compute_totals", store=False)
    achievement_pct = fields.Float(
        compute="_compute_totals", store=False, digits=(16, 2)
    )
    estimated_purchase_value = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_totals",
        store=False,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("po_created", "PO Created"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Linked documents (stat buttons)
    # ------------------------------------------------------------------
    po_id = fields.Many2one("purchase.order", copy=False, readonly=True)
    po_count = fields.Integer(compute="_compute_links")
    receipt_count = fields.Integer(compute="_compute_links")
    batch_count = fields.Integer(compute="_compute_links")

    # Intercompany — SO raised by UAE company that triggered this Egypt evaluation
    intercompany_so_id = fields.Many2one(
        "sale.order",
        string="Intercompany Sale Order",
        copy=False,
        help=(
            "The Sale Order automatically created in this company when the "
            "UAE/buying company raised an Intercompany Purchase Order. "
            "Link manually after the SO is created by Odoo's intercompany rules."
        ),
    )
    intercompany_demanded_qty = fields.Float(
        related="intercompany_so_id.amount_untaxed",
        string="IC Ordered Value",
        readonly=True,
        help="Untaxed value of the linked intercompany Sales Order.",
    )

    note = fields.Html()

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("farm_id")
    def _onchange_farm_id(self):
        """Pre-fill vendor from farm owner when farm is selected."""
        if self.farm_id and self.farm_id.partner_id:
            self.partner_id = self.farm_id.partner_id

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------
    @api.depends(
        "farm_expected_qty",
        "line_ids.expected_qty",
        "line_ids.estimated_unit_price",
        "line_ids.actual_qty",
        "line_ids.variance_qty",
    )
    def _compute_totals(self):
        """Aggregate totals from evaluation lines."""
        for rec in self:
            rec.expected_total_qty = sum(rec.line_ids.mapped("expected_qty"))
            rec.actual_total_qty = sum(rec.line_ids.mapped("actual_qty"))
            rec.variance_qty = rec.actual_total_qty - rec.expected_total_qty
            rec.achievement_pct = (
                (rec.actual_total_qty / rec.expected_total_qty * 100.0)
                if rec.expected_total_qty
                else 0.0
            )
            rec.estimated_purchase_value = sum(
                line.expected_qty * line.estimated_unit_price
                for line in rec.line_ids
            )

    def _compute_links(self):
        """Count linked POs, receipts, and batches for stat buttons."""
        Purchase = self.env["purchase.order"]
        Picking = self.env["stock.picking"]
        Batch = self.env["agx.batch"]
        for rec in self:
            rec.po_count = Purchase.search_count(
                [("agx_evaluation_id", "=", rec.id)]
            )
            rec.receipt_count = Picking.search_count(
                [
                    ("agx_evaluation_id", "=", rec.id),
                    ("picking_type_id.code", "=", "incoming"),
                ]
            )
            rec.batch_count = Batch.search_count(
                [("evaluation_id", "=", rec.id)]
            )

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Auto-assign sequence reference on creation."""
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("agx.evaluation")
                    or _("New")
                )
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_approve(self):
        self.write({"state": "approved"})

    def action_reset_draft(self):
        self.write({"state": "draft"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    # ------------------------------------------------------------------
    # Business actions
    # ------------------------------------------------------------------
    def action_create_purchase_order(self):
        """Create a purchase order from the evaluation lines.

        One PO per evaluation (stored in ``po_id``).  If a PO already
        exists, navigate to it instead.  Grade and size information is
        stored in the PO line description so it is visible on the PDF.
        """
        self.ensure_one()
        if not self.partner_id:
            raise UserError(
                _("Please select a vendor before creating a purchase order.")
            )
        if self.po_id:
            return self.action_view_purchase_order()

        order_lines = []
        for line in self.line_ids:
            if not line.product_id or not line.expected_qty:
                continue
            # Build a descriptive name including grade/size so the buyer
            # knows exactly what they are ordering.
            description_parts = [line.product_id.display_name]
            if line.grade_id:
                description_parts.append("Grade {}".format(line.grade_id.name))
            if line.size_id:
                description_parts.append("Size {}".format(line.size_id.name))
            description = " / ".join(description_parts)

            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "name": description,
                        "product_qty": line.expected_qty,
                        "product_uom_id": (
                            line.uom_id or line.product_id.uom_id
                        ).id,
                        "price_unit": line.estimated_unit_price,
                        "date_planned": fields.Datetime.now(),
                    },
                )
            )

        if not order_lines:
            raise UserError(
                _(
                    "Please add at least one evaluation line with a product "
                    "and quantity before creating a purchase order."
                )
            )

        po_vals = {
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
            "date_order": fields.Datetime.now(),
            "agx_evaluation_id": self.id,
            "order_line": order_lines,
        }

        # Note: analytic_account_id is not on purchase.order header in Odoo 19.
        # Analytic distribution is handled at the order line level instead.

        po = self.env["purchase.order"].create(po_vals)
        self.write({"po_id": po.id, "state": "po_created"})
        return self.action_view_purchase_order()

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------
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
            "domain": [
                ("agx_evaluation_id", "=", self.id),
                ("picking_type_id.code", "=", "incoming"),
            ],
        }

    def action_view_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Production Batches"),
            "res_model": "agx.batch",
            "view_mode": "list,form",
            "domain": [("evaluation_id", "=", self.id)],
        }


class AgxEvaluationLine(models.Model):
    """One line per expected grade/size combination on a farm evaluation.

    ``expected_qty`` is computed from the farm-level total and the
    expected percentage for this grade/size.

    ``actual_qty`` is computed dynamically from all batch output records
    linked to this evaluation that match the same product/grade/size.
    This gives a real-time achievement % with no manual data entry.
    """

    _name = "agx.evaluation.line"
    _description = "Farm Evaluation Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    evaluation_id = fields.Many2one(
        "agx.evaluation", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="evaluation_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="evaluation_id.currency_id", store=True, readonly=True
    )

    # ------------------------------------------------------------------
    # Product / Grade / Size
    # ------------------------------------------------------------------
    product_id = fields.Many2one(
        "product.product",
        required=True,
        help="Finished-goods product (should be a product variant when Grade/Size variants are configured).",
    )
    grade_id = fields.Many2one(
        "agx.grade",
        help="Expected quality grade for this line.",
    )
    size_id = fields.Many2one(
        "agx.size",
        help="Expected carton size for this line.",
    )

    # ------------------------------------------------------------------
    # Quantities
    # ------------------------------------------------------------------
    expected_ratio = fields.Float(
        string="Expected %",
        help="Expected yield percentage of this grade/size from the total farm qty.",
    )
    expected_qty = fields.Float(
        compute="_compute_expected_qty",
        store=True,
        help="Computed: farm_expected_qty × (expected_ratio / 100).",
    )
    actual_qty = fields.Float(
        compute="_compute_actuals",
        store=True,
        help="Sum of batch output quantities matching this grade/size.",
    )
    variance_qty = fields.Float(
        compute="_compute_actuals",
        store=True,
        help="actual_qty − expected_qty.",
    )
    achievement_pct = fields.Float(
        compute="_compute_actuals",
        store=True,
        digits=(16, 2),
        help="(actual_qty / expected_qty) × 100.",
    )
    uom_id = fields.Many2one("uom.uom", string="UoM")
    estimated_unit_price = fields.Monetary(currency_field="currency_id")
    note = fields.Char()

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends("expected_ratio", "evaluation_id.farm_expected_qty")
    def _compute_expected_qty(self):
        """Derive expected qty from the farm total and the ratio."""
        for rec in self:
            rec.expected_qty = (
                (rec.evaluation_id.farm_expected_qty or 0.0)
                * ((rec.expected_ratio or 0.0) / 100.0)
            )

    @api.depends("evaluation_id", "grade_id", "size_id")
    def _compute_actuals(self):
        """Compute actual produced qty from linked batch outputs.

        Matches by (evaluation_id, grade_id, size_id) — NOT by product_id.
        Reason: evaluation lines reference the RAW input product, while
        batch outputs reference the FINISHED product variant.  The common
        dimensions are grade and size.

        Uses a bulk fetch + lookup dict for performance (no N+1 queries).
        """
        if not self:
            return

        eval_ids = self.mapped("evaluation_id").ids
        if not eval_ids:
            for rec in self:
                rec.actual_qty = 0.0
                rec.variance_qty = -rec.expected_qty
                rec.achievement_pct = 0.0
            return

        # Fetch all relevant batch outputs in one query
        outputs = self.env["agx.batch.output"].search(
            [("batch_id.evaluation_id", "in", eval_ids)]
        )

        # Build lookup: (eval_id, grade_id, size_id) → total qty
        # This matches evaluation lines by grade/size regardless of product
        lookup = {}
        for o in outputs:
            key = (
                o.batch_id.evaluation_id.id,
                o.grade_id.id or False,
                o.size_id.id or False,
            )
            lookup[key] = lookup.get(key, 0.0) + o.qty

        for rec in self:
            key = (
                rec.evaluation_id.id,
                rec.grade_id.id or False,
                rec.size_id.id or False,
            )
            actual = lookup.get(key, 0.0)
            rec.actual_qty = actual
            rec.variance_qty = actual - rec.expected_qty
            rec.achievement_pct = (
                (actual / rec.expected_qty * 100.0)
                if rec.expected_qty
                else 0.0
            )

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Default UoM from product when product changes."""
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id


# ════════════════════════════════════════════════════════════════════
# QC / Inspection
# ════════════════════════════════════════════════════════════════════
class AgxQcInspection(models.Model):
    """Quality Control Inspection record.

    A QC check can be linked to:
      - A Goods Receipt (incoming — raw produce quality)
      - A Production Batch (in-process — grading/packing quality)
      - An Export Shipment (pre-shipment — final check before loading)

    Each inspection has a result and may generate rejection lines
    that feed back into batch scrap or shipment decisions.

    Inspection stages:
      draft      → inspector fills the form
      passed     → all checks within tolerance → proceed
      failed     → issues found → see rejection_ids
      conditional → minor issues, proceed with notes
    """

    _name = "agx.qc.inspection"
    _description = "QC / Quality Inspection"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "inspection_date desc, id desc"

    name = fields.Char(
        default=lambda self: _("New"),
        copy=False, readonly=True,
    )
    inspection_date = fields.Date(
        default=fields.Date.context_today, required=True, tracking=True
    )
    company_id = fields.Many2one(
        "res.company", required=True,
        default=lambda self: self.env.company, index=True,
    )
    inspector_id = fields.Many2one(
        "res.users", string="Inspector",
        default=lambda self: self.env.user, tracking=True,
    )

    # Link to ONE of these (exclusive)
    picking_id  = fields.Many2one(
        "stock.picking", string="Goods Receipt",
        domain="[('picking_type_id.code','=','incoming')]",
        help="Link to incoming goods receipt for raw material inspection.",
    )
    batch_id    = fields.Many2one(
        "agx.batch", string="Production Batch",
        help="Link to batch for in-process quality check.",
    )
    shipment_id = fields.Many2one(
        "agx.shipment", string="Export Shipment",
        help="Link to shipment for pre-loading final inspection.",
    )

    inspection_type = fields.Selection(
        [
            ("incoming",    "Incoming Goods (Raw Material)"),
            ("in_process",  "In-Process (Packing / Grading)"),
            ("pre_shipment","Pre-Shipment (Final Check)"),
        ],
        required=True, default="incoming", tracking=True,
    )
    result = fields.Selection(
        [
            ("passed",      "Passed"),
            ("failed",      "Failed"),
            ("conditional", "Conditional Pass"),
        ],
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft",  "Draft"),
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("conditional", "Conditional"),
        ],
        default="draft", tracking=True,
    )

    # Checklist lines
    line_ids = fields.One2many(
        "agx.qc.inspection.line", "inspection_id", string="Checklist"
    )
    rejection_ids = fields.One2many(
        "agx.qc.rejection", "inspection_id", string="Rejections / Issues"
    )

    overall_score = fields.Float(
        compute="_compute_score", store=True,
        help="Average score across all checklist lines (0–100).",
    )
    total_rejected_qty = fields.Float(
        compute="_compute_rejections", store=True,
        help="Sum of rejected quantities across all rejection lines.",
    )
    note = fields.Html()

    @api.depends("line_ids.score")
    def _compute_score(self):
        for rec in self:
            lines = rec.line_ids.filtered(lambda l: l.score is not False)
            rec.overall_score = (
                sum(lines.mapped("score")) / len(lines) if lines else 0.0
            )

    @api.depends("rejection_ids.rejected_qty")
    def _compute_rejections(self):
        for rec in self:
            rec.total_rejected_qty = sum(
                rec.rejection_ids.mapped("rejected_qty")
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("agx.qc.inspection")
                    or _("New")
                )
        return super().create(vals_list)

    def action_pass(self):
        self.write({"state": "passed", "result": "passed"})

    def action_fail(self):
        self.write({"state": "failed", "result": "failed"})

    def action_conditional(self):
        self.write({"state": "conditional", "result": "conditional"})

    def action_reset(self):
        self.write({"state": "draft", "result": False})


class AgxQcInspectionLine(models.Model):
    """One checklist item on a QC Inspection."""

    _name = "agx.qc.inspection.line"
    _description = "QC Inspection Checklist Line"
    _order = "sequence, id"

    sequence     = fields.Integer(default=10)
    inspection_id = fields.Many2one("agx.qc.inspection", required=True, ondelete="cascade")
    criterion    = fields.Char(required=True, string="Criterion / Check")
    standard     = fields.Char(help="Expected standard or tolerance (e.g. Brix ≥ 9.5)")
    actual_value = fields.Char(help="Measured / observed value")
    passed       = fields.Boolean(default=True)
    score        = fields.Float(
        digits=(16, 1),
        help="Score 0–100 for this criterion.",
    )
    note         = fields.Char()


class AgxQcRejection(models.Model):
    """One rejection item found during a QC Inspection."""

    _name = "agx.qc.rejection"
    _description = "QC Rejection Line"
    _order = "sequence, id"

    sequence      = fields.Integer(default=10)
    inspection_id = fields.Many2one("agx.qc.inspection", required=True, ondelete="cascade")
    product_id    = fields.Many2one("product.product")
    grade_id      = fields.Many2one("agx.grade")
    size_id       = fields.Many2one("agx.size")
    lot_id        = fields.Many2one("stock.lot")
    rejection_reason = fields.Selection(
        [
            ("size_out_of_spec",  "Size Out of Specification"),
            ("colour_defect",     "Colour Defect"),
            ("disease",           "Disease / Mould"),
            ("physical_damage",   "Physical Damage"),
            ("foreign_material",  "Foreign Material"),
            ("labelling",         "Labelling / Packaging Issue"),
            ("other",             "Other"),
        ],
        required=True,
    )
    rejected_qty  = fields.Float(required=True, default=1.0)
    uom_id        = fields.Many2one("uom.uom")
    disposition   = fields.Selection(
        [
            ("scrap",     "Scrap / Destroy"),
            ("downgrade", "Downgrade to Lower Grade"),
            ("repack",    "Repack / Rework"),
            ("return",    "Return to Supplier"),
        ],
        default="scrap",
    )
    note = fields.Char()
