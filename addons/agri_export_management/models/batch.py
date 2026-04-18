# -*- coding: utf-8 -*-
"""
batch.py — Production Batch Models
====================================
A batch represents one production run: raw produce goes in (inputs),
processed/packed cartons come out (outputs).

Stock movement flow (triggered by ``action_done``)
---------------------------------------------------
  1. CONSUME picking  (internal):
       Raw Material Location → Production Location
       One picking per distinct source location to keep the transfer log clean.

  2. OUTPUT picking  (internal):
       Production Location → Finished Goods Location
       One picking covering all output lines.

  3. (Optional) COLD STORAGE picking  (internal):
       Finished Goods Location → Cold Storage Location
       Triggered separately via ``action_move_to_cold_storage`` after Done.

Important: the Production Location MUST have ``usage = 'production'``
in Odoo's stock.location configuration.  Odoo then treats it as a
virtual location that auto-zeros — raw material consumed there does NOT
accumulate as residual stock.

Product Variant integration
---------------------------
Output lines carry ``product_id`` (a ``product.product`` — the variant),
``grade_id``, and ``size_id``.  When variants are properly configured
(see Initialize Product Attributes wizard), the variant IS the grade+size
combination, so stock reports show e.g. 'Orange / Grade A / Size 40'
separately.  The grade_id / size_id fields remain for filtering and
backward compatibility.

Costing
-------
Raw material cost is derived from PO prices via the linked evaluation's
incoming receipts.  If no receipts are found, ``standard_price`` is used
as a fallback.  Operation cost and other costs are entered manually.
Cost per output unit is allocated proportionally by relative sales value
(sales_price × qty) across output lines — standard absorption costing.

Season / Analytic
-----------------
The batch carries ``season_id``; the linked season's analytic account
is stamped on the output picking so Odoo's analytic reports include
production costs in the season P&L automatically.
"""

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AgxBatch(models.Model):
    """Production batch — one packing/processing run."""

    _name = "agx.batch"
    _description = "Production Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "batch_date desc, id desc"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(
        default=lambda self: _("New"),
        copy=False,
        readonly=True,
        help="Auto-generated from the AGX Batch sequence.",
    )
    batch_date = fields.Date(
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
    # References
    # ------------------------------------------------------------------
    evaluation_id = fields.Many2one(
        "agx.evaluation",
        tracking=True,
        help="Farm evaluation this batch is processing produce from.",
    )
    season_id = fields.Many2one(
        "agx.season",
        string="Season",
        tracking=True,
        index=True,
        help="Populated from the evaluation's season; can be set directly.",
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Input / Output lines
    # ------------------------------------------------------------------
    input_line_ids = fields.One2many(
        "agx.batch.input", "batch_id", string="Inputs", copy=True
    )
    output_line_ids = fields.One2many(
        "agx.batch.output", "batch_id", string="Outputs", copy=True
    )

    # ------------------------------------------------------------------
    # Quantity totals
    # ------------------------------------------------------------------
    input_qty = fields.Float(
        compute="_compute_qty_totals",
        store=True,
        help="Total raw material quantity consumed.",
    )
    output_qty = fields.Float(
        compute="_compute_qty_totals",
        store=True,
        help="Total finished-goods quantity produced.",
    )
    variance_qty = fields.Float(
        compute="_compute_qty_totals",
        store=True,
        help="output_qty − input_qty (positive = gain, negative = loss).",
    )

    # ------------------------------------------------------------------
    # Costing fields
    # ------------------------------------------------------------------
    manual_raw_material_cost = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Manual override for raw material cost when no PO/receipt is linked.",
    )
    manual_operation_cost = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Labour, electricity, and other direct production costs.",
    )
    manual_other_cost = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Packing materials, fuel, or any other indirect costs.",
    )
    actual_raw_material_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_actual_costs",
        store=True,
        help="Computed from PO prices via linked evaluation receipts.",
    )
    actual_operation_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_actual_costs",
        store=True,
        help="Reserved for future automated operation cost sourcing.",
    )
    effective_allocable_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_costing",
        store=True,
        help="Total cost to be spread across output lines.",
    )
    total_relative_sales_value = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_costing",
        store=True,
        help="Sum of (qty × sales_price_unit) across all output lines.",
    )
    costing_status = fields.Selection(
        [("manual", "Manual"), ("actual", "Actual")],
        compute="_compute_costing",
        store=True,
        help="'Actual' when PO-derived costs are available; 'Manual' otherwise.",
    )
    cost_basis_note = fields.Char(
        compute="_compute_costing",
        store=True,
        help="Human-readable explanation of which cost source is active.",
    )

    # ------------------------------------------------------------------
    # Stock move links
    # ------------------------------------------------------------------
    stock_move_ids = fields.One2many(
        "stock.move",
        "agx_batch_id",
        string="Stock Moves",
        readonly=True,
        help="All stock moves generated when this batch was posted.",
    )
    stock_picking_ids = fields.One2many(
        "stock.picking",
        "agx_batch_id",
        string="Stock Transfers",
        readonly=True,
        help="All stock transfers generated by this batch.",
    )
    stock_move_count = fields.Integer(compute="_compute_stock_counts")
    stock_move_state = fields.Selection(
        [
            ("none", "No Transfers"),
            ("partial", "Partial"),
            ("done", "Done"),
        ],
        compute="_compute_stock_counts",
    )
    receipt_count = fields.Integer(compute="_compute_stock_counts")

    # ------------------------------------------------------------------
    # Computed: qty totals
    # ------------------------------------------------------------------
    @api.depends("input_line_ids.qty", "output_line_ids.qty")
    def _compute_qty_totals(self):
        for rec in self:
            rec.input_qty = sum(rec.input_line_ids.mapped("qty"))
            rec.output_qty = sum(rec.output_line_ids.mapped("qty"))
            rec.variance_qty = rec.output_qty - rec.input_qty

    # ------------------------------------------------------------------
    # Computed: stock counts
    # ------------------------------------------------------------------
    @api.depends(
        "evaluation_id",
        "stock_picking_ids.state",
        "stock_move_ids.state",
    )
    def _compute_stock_counts(self):
        Picking = self.env["stock.picking"]
        for rec in self:
            pickings = rec.stock_picking_ids
            rec.stock_move_count = len(pickings)
            done_count = len(pickings.filtered(lambda p: p.state == "done"))
            if not pickings:
                rec.stock_move_state = "none"
            elif done_count == len(pickings):
                rec.stock_move_state = "done"
            else:
                rec.stock_move_state = "partial"
            rec.receipt_count = (
                Picking.search_count(
                    [
                        ("agx_evaluation_id", "=", rec.evaluation_id.id),
                        ("picking_type_id.code", "=", "incoming"),
                        ("state", "=", "done"),
                    ]
                )
                if rec.evaluation_id
                else 0
            )

    # ------------------------------------------------------------------
    # Computed: actual costs from PO receipts
    # ------------------------------------------------------------------
    @api.depends(
        "evaluation_id",
        "input_line_ids.qty",
        "input_line_ids.product_id",
        "input_line_ids.lot_id",
    )
    def _compute_actual_costs(self):
        """Derive raw material cost from PO prices on linked receipts.

        For each input line we search done incoming receipts linked to
        the evaluation, match moves by product (and lot if specified),
        and price the consumed qty at the PO line price.  Any unmatched
        qty falls back to ``standard_price``.
        """
        qty_field = (
            "quantity"
            if "quantity" in self.env["stock.move.line"]._fields
            else "qty_done"
        )
        for rec in self:
            raw_cost = 0.0
            if rec.evaluation_id:
                pickings = rec._get_done_receipts()
                for line in rec.input_line_ids:
                    remaining = line.qty or 0.0
                    if not remaining:
                        continue
                    moves = pickings.mapped("move_ids").filtered(
                        lambda m: m.product_id == line.product_id
                        and m.state == "done"
                    )
                    for move in moves:
                        move_qty = (
                            move.quantity
                            if hasattr(move, "quantity")
                            else move.product_uom_qty
                        )
                        # Refine qty to specific lot if requested
                        if line.lot_id and move.move_line_ids:
                            lot_lines = move.move_line_ids.filtered(
                                lambda ml: ml.lot_id == line.lot_id
                            )
                            ml_qty = sum(lot_lines.mapped(qty_field))
                            move_qty = ml_qty or move_qty
                        if move_qty <= 0:
                            continue
                        take_qty = min(remaining, move_qty)
                        price_unit = (
                            move.purchase_line_id.price_unit
                            if move.purchase_line_id
                            else line.product_id.standard_price
                        )
                        raw_cost += take_qty * price_unit
                        remaining -= take_qty
                        if remaining <= 0:
                            break
                    # Fallback to standard price for any unmatched qty
                    if remaining > 0:
                        raw_cost += remaining * (
                            line.product_id.standard_price or 0.0
                        )
            else:
                # No evaluation linked — use standard price directly
                raw_cost = sum(
                    (line.qty or 0.0) * (line.product_id.standard_price or 0.0)
                    for line in rec.input_line_ids
                )
            rec.actual_raw_material_cost = raw_cost
            # Operation cost automated sourcing not yet implemented
            rec.actual_operation_cost = 0.0

    # ------------------------------------------------------------------
    # Computed: costing summary
    # ------------------------------------------------------------------
    @api.depends(
        "manual_raw_material_cost",
        "manual_operation_cost",
        "manual_other_cost",
        "actual_raw_material_cost",
        "actual_operation_cost",
        "output_line_ids.sales_value",
    )
    def _compute_costing(self):
        """Determine effective allocable cost and costing status.

        Prefers actual costs (from PO receipts) over manual costs.
        manual_other_cost (packing materials etc.) is always added on top.
        """
        for rec in self:
            use_actual = bool(
                rec.actual_raw_material_cost or rec.actual_operation_cost
            )
            raw_cost = (
                rec.actual_raw_material_cost
                if use_actual
                else rec.manual_raw_material_cost
            )
            op_cost = (
                rec.actual_operation_cost
                if use_actual
                else rec.manual_operation_cost
            )
            rec.effective_allocable_cost = (
                raw_cost + op_cost + rec.manual_other_cost
            )
            rec.total_relative_sales_value = sum(
                rec.output_line_ids.mapped("sales_value")
            )
            rec.costing_status = "actual" if use_actual else "manual"
            if use_actual and rec.evaluation_id:
                rec.cost_basis_note = _(
                    "Based on done incoming receipts linked to the selected "
                    "evaluation, with fallback to product standard price."
                )
            else:
                rec.cost_basis_note = _("Based on manual fallback costs.")

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Auto-assign sequence and inherit season from evaluation."""
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("agx.batch")
                    or _("New")
                )
        records = super().create(vals_list)
        # Inherit season from evaluation if not explicitly set
        for rec in records:
            if not rec.season_id and rec.evaluation_id and rec.evaluation_id.season_id:
                rec.season_id = rec.evaluation_id.season_id
        return records

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("evaluation_id")
    def _onchange_evaluation_id(self):
        """Load inputs from done receipts and inherit season."""
        for rec in self:
            if rec.evaluation_id and rec.evaluation_id.season_id:
                rec.season_id = rec.evaluation_id.season_id
            rec._load_inputs_from_evaluation_receipts(onchange_mode=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_done_receipts(self):
        """Return done incoming pickings linked to this batch's evaluation."""
        self.ensure_one()
        if not self.evaluation_id:
            return self.env["stock.picking"]
        return self.env["stock.picking"].search(
            [
                ("agx_evaluation_id", "=", self.evaluation_id.id),
                ("picking_type_id.code", "=", "incoming"),
                ("state", "=", "done"),
                ("company_id", "=", self.company_id.id),
            ]
        )

    def _load_inputs_from_evaluation_receipts(self, onchange_mode=False):
        """Populate input lines from done incoming receipts.

        Groups move lines by (product, lot, uom) so each unique
        lot gets its own input line for accurate cost tracking.
        """
        qty_field = (
            "quantity"
            if "quantity" in self.env["stock.move.line"]._fields
            else "qty_done"
        )
        for rec in self:
            if not rec.evaluation_id:
                continue
            pickings = rec._get_done_receipts()
            if not pickings:
                if onchange_mode:
                    rec.input_line_ids = [(5, 0, 0)]
                continue

            # Group by (product_id, lot_id, uom_id)
            grouped = defaultdict(
                lambda: {
                    "qty": 0.0,
                    "product_id": False,
                    "lot_id": False,
                    "uom_id": False,
                }
            )
            for picking in pickings:
                if picking.move_line_ids:
                    for ml in picking.move_line_ids.filtered(
                        lambda l: l.product_id
                        and (
                            getattr(l, "quantity", 0.0)
                            or getattr(l, "qty_done", 0.0)
                        )
                    ):
                        qty = getattr(ml, qty_field, 0.0)
                        uom_id = (
                            ml.product_uom_id.id
                            if ml.product_uom_id
                            else ml.product_id.uom_id.id
                        )
                        key = (
                            ml.product_id.id,
                            ml.lot_id.id if ml.lot_id else False,
                            uom_id,
                        )
                        grouped[key]["qty"] += qty
                        grouped[key]["product_id"] = ml.product_id.id
                        grouped[key]["lot_id"] = (
                            ml.lot_id.id if ml.lot_id else False
                        )
                        grouped[key]["uom_id"] = uom_id
                else:
                    # Fallback to move level if no detailed move lines
                    for move in picking.move_ids.filtered(
                        lambda m: m.product_id and m.state == "done"
                    ):
                        qty = (
                            move.quantity
                            if hasattr(move, "quantity")
                            else move.product_uom_qty
                        )
                        uom_id = (
                            move.product_uom.id
                            if move.product_uom
                            else move.product_id.uom_id.id
                        )
                        key = (move.product_id.id, False, uom_id)
                        grouped[key]["qty"] += qty
                        grouped[key]["product_id"] = move.product_id.id
                        grouped[key]["lot_id"] = False
                        grouped[key]["uom_id"] = uom_id

            commands = [(5, 0, 0)]
            for vals in grouped.values():
                commands.append((0, 0, vals))
            rec.input_line_ids = commands

    # ------------------------------------------------------------------
    # MRP integration
    # ------------------------------------------------------------------
    def action_create_mrp_production(self):
        """Create a Manufacturing Order linked to this batch.

        Only available when agx_use_mrp_production is enabled in Settings.
        Does NOT replace the batch stock moves workflow — both can coexist.
        Existing batches without an MO are completely unaffected.

        The MO is created in draft state so the user can configure
        Bill of Materials, workcenter, etc. before confirming.
        """
        self.ensure_one()
        if self.mrp_production_id:
            return self._action_view_mrp_production()

        # Guard: ensure MRP module is installed
        if self.env.get("mrp.production") is None:
            from odoo.exceptions import UserError
            raise UserError(
                "The Manufacturing (MRP) module is not installed. "
                "Please install it from Apps first, then retry."
            )

        # Find the main output product for the MO
        main_output = self.output_line_ids.sorted(
            lambda l: l.qty, reverse=True
        )[:1]
        if not main_output:
            from odoo.exceptions import UserError
            raise UserError(
                "Please add at least one output line before creating a Manufacturing Order."
            )

        product = main_output.product_id
        mo_vals = {
            "product_id": product.id,
            "product_qty": sum(self.output_line_ids.mapped("qty")),
            "product_uom_id": (main_output.uom_id or product.uom_id).id,
            "company_id": self.company_id.id,
            "origin": self.name,
        }
        # Try to find a BoM for the product
        bom = self.env["mrp.bom"].search(
            [
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                ("company_id", "in", [False, self.company_id.id]),
            ],
            limit=1,
        )
        if bom:
            mo_vals["bom_id"] = bom.id

        mo = self.env["mrp.production"].create(mo_vals)
        self.mrp_production_id = mo.id
        return self._action_view_mrp_production()

    def _action_view_mrp_production(self):
        """Open the linked Manufacturing Order."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Manufacturing Order",
            "res_model": "mrp.production",
            "res_id": self.mrp_production_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_start(self):
        self.write({"state": "in_progress"})

    def _validate_before_done(self):
        """Business validations before marking a batch as Done.

        Raises UserError for:
          - No output lines
          - Output qty > input qty (implausible without scrap explanation)
          - Missing lots on outputs
        """
        self.ensure_one()
        errors = []

        if not self.output_line_ids:
            errors.append("Please add at least one output line before marking Done.")

        if not self.input_line_ids:
            errors.append("No input lines found. Please add raw material inputs.")

        # Outputs > inputs without scrap explanation
        if (self.output_qty > self.input_qty * 1.05  # 5% tolerance
                and not self.scrap_line_ids):
            errors.append(
                f"Output qty ({self.output_qty:.0f}) exceeds input qty "
                f"({self.input_qty:.0f}) by more than 5%. "
                "If this is expected, please add a note. "
                "If not, check your input/output figures."
            )

        # Warn if scrap is very high (> 30%) — non-blocking chatter warning
        if self.input_qty and self.total_scrap_qty:
            scrap_ratio = self.total_scrap_qty / self.input_qty
            if scrap_ratio > 0.30:
                self.message_post(
                    body=(
                        f"⚠️ High scrap ratio: {scrap_ratio*100:.1f}% of input "
                        "was recorded as waste/loss. Please verify."
                    )
                )

        if errors:
            raise UserError("  |  ".join(["* " + str(e) for e in errors]))

    def action_done(self):
        """Validate the batch and post stock movements.

        Steps:
          1. Load inputs from receipts if lines are empty.
          2. Validate at least one input and one output exist.
          3. Auto-generate lot numbers for unlotted outputs (if configured).
          4. Check sufficient stock is available for all inputs.
          5. Post consume + output stock pickings.
          6. Mark batch as Done.
        """
        for rec in self:
            if not rec.input_line_ids:
                rec._load_inputs_from_evaluation_receipts(onchange_mode=False)
            if not rec.input_line_ids:
                raise UserError(
                    _(
                        "Please load or add at least one input line "
                        "before marking the batch as done."
                    )
                )
            if not rec.output_line_ids:
                raise UserError(
                    _(
                        "Please add at least one output line "
                        "before marking the batch as done."
                    )
                )
            rec._ensure_output_lots()
            rec._validate_stock_posting()
            rec._post_stock_pickings()
            rec.state = "done"
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_move_to_cold_storage(self):
        """Create an internal transfer from Finished Goods → Cold Storage.

        Only available after the batch is Done and the company has a
        cold storage location configured.
        """
        for rec in self:
            cold_loc = rec.company_id.agx_cold_storage_location_id
            if not cold_loc:
                raise UserError(
                    _(
                        "No Cold Storage Location configured. "
                        "Please set it in Agricultural Export Settings."
                    )
                )
            finished_loc = (
                rec.company_id.agx_finished_goods_location_id
                or rec._get_default_stock_location()
            )
            internal_type = rec._get_internal_picking_type()
            if not internal_type:
                raise UserError(
                    _("Please configure an internal transfer type in Settings.")
                )
            picking = rec._create_picking(
                picking_type=internal_type,
                location_id=finished_loc,
                location_dest_id=cold_loc,
                origin="{} / Cold Storage".format(rec.name),
                flow_type="batch_output",
            )
            Move = self.env["stock.move"]
            MoveLine = self.env["stock.move.line"]
            for line in rec.output_line_ids.filtered(
                lambda l: l.product_id and l.qty > 0
            ):
                move = Move.create(
                    {
                        "description_picking": "{} / ColdStore / {}".format(
                            rec.name, line.product_id.display_name
                        ),
                        "company_id": rec.company_id.id,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.qty,
                        "product_uom": (
                            line.uom_id or line.product_id.uom_id
                        ).id,
                        "location_id": finished_loc.id,
                        "location_dest_id": cold_loc.id,
                        "agx_batch_id": rec.id,
                        "agx_flow_type": "output",
                        "origin": rec.name,
                        "picking_id": picking.id,
                    }
                )
                picking.action_confirm()
                move.move_line_ids.unlink()
                MoveLine.create(
                    rec._prepare_move_line_vals(
                        move=move,
                        product=line.product_id,
                        uom=(line.uom_id or line.product_id.uom_id),
                        location_id=finished_loc,
                        location_dest_id=cold_loc,
                        qty=line.qty,
                        lot_id=line.lot_id,
                    )
                )
            rec._validate_picking(picking)
        return True

    # ------------------------------------------------------------------
    # Stock helpers
    # ------------------------------------------------------------------
    def _ensure_output_lots(self):
        """Auto-generate lot numbers for output lines missing one."""
        for rec in self:
            if not rec.company_id.agx_auto_generate_lot_numbers:
                continue
            for line in rec.output_line_ids.filtered(
                lambda l: not l.lot_id and l.product_id
            ):
                lot_name = self.env["ir.sequence"].next_by_code(
                    "stock.lot.serial"
                ) or "{}-{}".format(
                    rec.name,
                    line.product_id.default_code or str(line.product_id.id),
                )
                line.lot_id = self.env["stock.lot"].create(
                    {
                        "name": lot_name,
                        "product_id": line.product_id.id,
                        "company_id": rec.company_id.id,
                    }
                ).id

    def _get_default_stock_location(self):
        """Return first internal location for this company (deterministic)."""
        self.ensure_one()
        return self.env["stock.location"].search(
            [
                ("company_id", "in", [False, self.company_id.id]),
                ("usage", "=", "internal"),
            ],
            limit=1,
            order="company_id desc, id",
        )

    def _get_default_production_location(self):
        """Return the production-usage location, falling back to internal."""
        self.ensure_one()
        return (
            self.env["stock.location"].search(
                [
                    ("company_id", "in", [False, self.company_id.id]),
                    ("usage", "=", "production"),
                ],
                limit=1,
                order="company_id desc, id",
            )
            or self._get_default_stock_location()
        )

    def _get_internal_picking_type(self):
        """Return the configured internal picking type."""
        self.ensure_one()
        return self.company_id.agx_internal_picking_type_id or self.env[
            "stock.picking.type"
        ].search(
            [
                ("code", "=", "internal"),
                ("company_id", "in", [False, self.company_id.id]),
            ],
            limit=1,
            order="company_id desc, id",
        )

    def _quantity_field_name(self, model):
        """Detect the correct quantity field name for the Odoo version."""
        return "quantity" if "quantity" in model._fields else "qty_done"

    def _get_line_available_qty(self, line):
        """Return available (unreserved) qty for an input line."""
        self.ensure_one()
        domain = [
            ("company_id", "=", self.company_id.id),
            ("product_id", "=", line.product_id.id),
            ("location_id.usage", "=", "internal"),
        ]
        if line.lot_id:
            domain.append(("lot_id", "=", line.lot_id.id))
        quants = self.env["stock.quant"].search(domain)
        return sum(
            max((q.quantity or 0.0) - (q.reserved_quantity or 0.0), 0.0)
            for q in quants
        )

    def _validate_stock_posting(self):
        """Pre-flight checks before posting stock movements.

        Raises UserError if:
        - Required locations or picking types are not configured.
        - This batch already has posted transfers (prevents duplicates).
        - Insufficient stock for any input line.
        """
        self.ensure_one()
        production_location = (
            self.company_id.agx_production_location_id
            or self._get_default_production_location()
        )
        finished_location = (
            self.company_id.agx_finished_goods_location_id
            or self._get_default_stock_location()
        )
        internal_type = self._get_internal_picking_type()
        if not production_location or not finished_location or not internal_type:
            raise UserError(
                _(
                    "Please configure Production Location, Finished Goods "
                    "Location, and Internal Transfer Type in Settings."
                )
            )
        # Guard against accidental re-posting
        if self.stock_picking_ids.filtered(lambda p: p.state == "done"):
            raise UserError(
                _(
                    "This batch already has posted stock transfers. "
                    "A done batch cannot be posted again to avoid "
                    "duplicate inventory movements."
                )
            )
        # Stock availability check
        for line in self.input_line_ids.filtered(
            lambda l: l.product_id and l.qty > 0
        ):
            available = self._get_line_available_qty(line)
            if available < line.qty:
                if line.lot_id:
                    raise UserError(
                        _(
                            "Not enough stock for %(product)s / Lot %(lot)s. "
                            "Needed: %(needed)s, Available: %(available)s"
                        )
                        % {
                            "product": line.product_id.display_name,
                            "lot": line.lot_id.display_name,
                            "needed": line.qty,
                            "available": available,
                        }
                    )
                raise UserError(
                    _(
                        "Not enough stock for %(product)s. "
                        "Needed: %(needed)s, Available: %(available)s"
                    )
                    % {
                        "product": line.product_id.display_name,
                        "needed": line.qty,
                        "available": available,
                    }
                )

    def _prepare_move_line_vals(
        self, move, product, uom, location_id, location_dest_id, qty, lot_id=False
    ):
        """Build stock.move.line create values for a single lot/qty pair."""
        vals = {
            "move_id": move.id,
            "product_id": product.id,
            "product_uom_id": uom.id,
            "location_id": location_id.id,
            "location_dest_id": location_dest_id.id,
        }
        vals[self._quantity_field_name(self.env["stock.move.line"])] = qty
        if lot_id:
            vals["lot_id"] = lot_id.id
        return vals

    def _input_source_location(self, line):
        """Determine the best source location for an input line.

        Priority:
          1. The actual quant location for the specific lot (most accurate).
          2. The destination of done receipts linked to the evaluation.
          3. The configured raw material location.
          4. First internal location (last resort).
        """
        self.ensure_one()
        if line.lot_id:
            quant = self.env["stock.quant"].search(
                [
                    ("product_id", "=", line.product_id.id),
                    ("lot_id", "=", line.lot_id.id),
                    ("company_id", "=", self.company_id.id),
                    ("location_id.usage", "=", "internal"),
                    ("quantity", ">", 0),
                ],
                limit=1,
            )
            if quant:
                return quant.location_id
        receipts = self._get_done_receipts()
        dests = receipts.mapped("location_dest_id")
        return dests[:1] if dests else (
            self.company_id.agx_raw_material_location_id
            or self._get_default_stock_location()
        )

    def _validate_picking(self, picking):
        """Validate a picking; force-done any remaining moves if needed."""
        res = picking.with_context(
            skip_immediate=True, skip_backorder=True
        ).button_validate()
        if isinstance(res, dict):
            pending = picking.move_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
            )
            if pending:
                pending._action_done()
        return True

    def _create_picking(
        self, picking_type, location_id, location_dest_id, origin, flow_type=False
    ):
        """Create a stock.picking with AGX context fields.

        ``flow_type`` is written directly at creation time so that the
        agx_flow_type field is correctly set before any move lines exist.
        Not setting it here would leave the field False because the
        compute fires before moves are created.
        """
        vals = {
            "picking_type_id": picking_type.id,
            "location_id": location_id.id,
            "location_dest_id": location_dest_id.id,
            "origin": origin,
            "company_id": self.company_id.id,
            "agx_batch_id": self.id,
        }
        if flow_type:
            vals["agx_flow_type"] = flow_type
        # Note: stock.picking has no analytic_account_id header field in Odoo 19.
        return self.env["stock.picking"].create(vals)

    def _post_stock_pickings(self):
        """Create and validate consume + output stock pickings.

        Consume pickings: one per distinct source location (groups input
        lines by their actual stock location to avoid cross-location moves).

        Output picking: one picking from production → finished goods
        covering all output lines.

        Returns all created stock.move records.
        """
        Move = self.env["stock.move"]
        MoveLine = self.env["stock.move.line"]
        all_created_moves = self.env["stock.move"]

        for rec in self:
            production_location = (
                rec.company_id.agx_production_location_id
                or rec._get_default_production_location()
            )
            finished_location = (
                rec.company_id.agx_finished_goods_location_id
                or rec._get_default_stock_location()
            )
            internal_type = rec._get_internal_picking_type()
            if not internal_type:
                raise UserError(
                    _("Please configure an internal transfer type in Settings.")
                )

            # ----------------------------------------------------------
            # STEP 1: Consume moves — raw → production
            # ----------------------------------------------------------
            input_groups = defaultdict(list)
            for line in rec.input_line_ids.filtered(
                lambda l: l.product_id and l.qty > 0
            ):
                source = rec._input_source_location(line)
                input_groups[source.id].append((source, line))

            for _src_id, grouped_lines in input_groups.items():
                source_location = grouped_lines[0][0]
                picking = rec._create_picking(
                    picking_type=internal_type,
                    location_id=source_location,
                    location_dest_id=production_location,
                    origin="{} / Consumption".format(rec.name),
                    flow_type="batch_consume",
                )
                pairs = []
                for _src, line in grouped_lines:
                    move = Move.create(
                        {
                            "description_picking": "{} / Consume / {}".format(
                                rec.name, line.product_id.display_name
                            ),
                            "company_id": rec.company_id.id,
                            "product_id": line.product_id.id,
                            "product_uom_qty": line.qty,
                            "product_uom": (
                                line.uom_id or line.product_id.uom_id
                            ).id,
                            "location_id": source_location.id,
                            "location_dest_id": production_location.id,
                            "agx_batch_id": rec.id,
                            "agx_flow_type": "consume",
                            "origin": rec.name,
                            "picking_id": picking.id,
                        }
                    )
                    all_created_moves |= move
                    pairs.append((move, line))

                picking.action_confirm()
                for move, line in pairs:
                    move.move_line_ids.unlink()
                    MoveLine.create(
                        rec._prepare_move_line_vals(
                            move=move,
                            product=line.product_id,
                            uom=(line.uom_id or line.product_id.uom_id),
                            location_id=source_location,
                            location_dest_id=production_location,
                            qty=line.qty,
                            lot_id=line.lot_id,
                        )
                    )
                rec._validate_picking(picking)

            # ----------------------------------------------------------
            # STEP 2: Output move — production → finished goods
            # ----------------------------------------------------------
            output_picking = rec._create_picking(
                picking_type=internal_type,
                location_id=production_location,
                location_dest_id=finished_location,
                origin="{} / Output".format(rec.name),
                flow_type="batch_output",
            )
            out_pairs = []
            for line in rec.output_line_ids.filtered(
                lambda l: l.product_id and l.qty > 0
            ):
                move = Move.create(
                    {
                        "description_picking": "{} / Output / {}".format(
                            rec.name, line.product_id.display_name
                        ),
                        "company_id": rec.company_id.id,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.qty,
                        "product_uom": (
                            line.uom_id or line.product_id.uom_id
                        ).id,
                        "location_id": production_location.id,
                        "location_dest_id": finished_location.id,
                        "agx_batch_id": rec.id,
                        "agx_flow_type": "output",
                        "origin": rec.name,
                        "picking_id": output_picking.id,
                    }
                )
                all_created_moves |= move
                out_pairs.append((move, line))

            output_picking.action_confirm()
            for move, line in out_pairs:
                move.move_line_ids.unlink()
                MoveLine.create(
                    rec._prepare_move_line_vals(
                        move=move,
                        product=line.product_id,
                        uom=(line.uom_id or line.product_id.uom_id),
                        location_id=production_location,
                        location_dest_id=finished_location,
                        qty=line.qty,
                        lot_id=line.lot_id,
                    )
                )
            rec._validate_picking(output_picking)

        return all_created_moves

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------
    def action_view_stock_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Transfers"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("agx_batch_id", "=", self.id)],
        }

    def action_view_receipts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Incoming Receipts"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [
                ("agx_evaluation_id", "=", self.evaluation_id.id),
                ("picking_type_id.code", "=", "incoming"),
            ],
        }


# ---------------------------------------------------------------------------
# Batch Input Line
# ---------------------------------------------------------------------------
class AgxBatchInput(models.Model):
    """One line of raw material input consumed in a batch.

    Lot tracking is critical here: the lot links back to the supplier
    receipt and drives the actual cost calculation in ``_compute_actual_costs``.
    """

    _name = "agx.batch.input"
    _description = "Batch Input"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one(
        "stock.lot",
        help="Specific supplier lot; used for cost derivation and traceability.",
    )
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    note = fields.Char()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id


# ---------------------------------------------------------------------------
# Batch Output Line
# ---------------------------------------------------------------------------
class AgxBatchOutput(models.Model):
    """One line of finished-goods output produced in a batch.

    Each output line represents one Grade + Size combination.
    When product variants are configured, ``product_id`` IS the variant
    (e.g. 'Orange / Grade A / Size 40') and grade_id / size_id are
    populated automatically from the variant's attribute values.

    Cost allocation uses relative sales value:
      allocated_cost = (batch.effective_allocable_cost)
                       × (this_line.sales_value / total_sales_value)
      cost_per_unit  = allocated_cost / qty
    """

    _name = "agx.batch.output"
    _description = "Batch Output"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    company_id = fields.Many2one(
        related="batch_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="batch_id.currency_id", store=True, readonly=True
    )

    # ------------------------------------------------------------------
    # Product / Grade / Size
    # ------------------------------------------------------------------
    product_id = fields.Many2one(
        "product.product",
        required=True,
        help=(
            "Use a product variant (e.g. 'Orange / Grade A / Size 40') "
            "so that this output is tracked as a distinct SKU in stock."
        ),
    )
    lot_id = fields.Many2one(
        "stock.lot",
        string="Output Lot",
        help="Lot number for this output carton group.",
    )
    grade_id = fields.Many2one(
        "agx.grade",
        help="Quality grade — populated from variant or selected manually.",
    )
    size_id = fields.Many2one(
        "agx.size",
        help="Carton size — populated from variant or selected manually.",
    )
    qty = fields.Float(required=True, default=1.0, help="Number of cartons.")
    uom_id = fields.Many2one("uom.uom")

    # ------------------------------------------------------------------
    # Pricing / costing
    # ------------------------------------------------------------------
    sales_price_unit = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Expected selling price per unit (used for cost allocation ratio).",
    )
    sales_value = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_cost_share",
        store=True,
        help="qty × sales_price_unit.",
    )
    relative_sales_ratio = fields.Float(
        compute="_compute_cost_share",
        store=True,
        digits=(16, 6),
        help="This line's share of total batch sales value.",
    )
    allocated_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_cost_share",
        store=True,
        help="Batch total cost × relative_sales_ratio.",
    )
    cost_per_unit = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_cost_share",
        store=True,
        help="allocated_cost ÷ qty.",
    )

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # ORM overrides — trigger evaluation line recompute
    # ------------------------------------------------------------------
    def _trigger_evaluation_recompute(self):
        """Invalidate evaluation line actual_qty when batch outputs change.

        When a batch output is created or modified, we need to recompute
        the linked evaluation lines so Actual Qty and Achievement % update.
        """
        eval_ids = self.mapped("batch_id.evaluation_id").filtered(bool)
        if eval_ids:
            eval_lines = self.env["agx.evaluation.line"].search(
                [("evaluation_id", "in", eval_ids.ids)]
            )
            # Invalidate the stored compute fields so they recalculate
            eval_lines.invalidate_recordset(["actual_qty", "variance_qty", "achievement_pct"])
            eval_lines._compute_actuals()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._trigger_evaluation_recompute()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ("qty", "grade_id", "size_id", "batch_id")):
            self._trigger_evaluation_recompute()
        return res

    def unlink(self):
        self._trigger_evaluation_recompute()
        return super().unlink()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Default UoM and auto-fill grade/size from variant attributes."""
        for rec in self:
            if not rec.product_id:
                continue
            rec.uom_id = rec.product_id.uom_id
            # Try to derive grade and size from the product variant's
            # attribute values, matching against agx.grade / agx.size records
            # that have their attribute_value_id configured.
            for ptav in rec.product_id.product_template_attribute_value_ids:
                av = ptav.product_attribute_value_id
                # Match grade
                grade = self.env["agx.grade"].search(
                    [("attribute_value_id", "=", av.id)], limit=1
                )
                if grade:
                    rec.grade_id = grade
                # Match size
                size = self.env["agx.size"].search(
                    [("attribute_value_id", "=", av.id)], limit=1
                )
                if size:
                    rec.size_id = size

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends(
        "qty",
        "sales_price_unit",
        "batch_id.effective_allocable_cost",
        "batch_id.total_relative_sales_value",
    )
    def _compute_cost_share(self):
        """Allocate batch cost to this line proportionally by sales value."""
        for rec in self:
            rec.sales_value = (rec.qty or 0.0) * (rec.sales_price_unit or 0.0)
            total = rec.batch_id.total_relative_sales_value or 0.0
            rec.relative_sales_ratio = (
                (rec.sales_value / total) if total else 0.0
            )
            rec.allocated_cost = (
                rec.batch_id.effective_allocable_cost or 0.0
            ) * rec.relative_sales_ratio
            rec.cost_per_unit = (
                (rec.allocated_cost / rec.qty) if rec.qty else 0.0
            )


class AgxBatchScrap(models.Model):
    """Waste / Scrap / Loss line on a Production Batch.

    Records any quantity that was consumed but did not become a
    finished output — spoilage, rejects, natural shrinkage, etc.

    scrap_qty is automatically deducted from batch costing so the
    effective_allocable_cost per output unit reflects real yield.

    Scrap types:
      natural_loss   — inevitable field shrinkage (dew, evaporation)
      quality_reject — failed grading / sizing at packing line
      damage         — mechanical or transit damage
      disease        — fungal / pest loss
      other          — anything else, requires reason text
    """

    _name = "agx.batch.scrap"
    _description = "Batch Scrap / Waste Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one(
        "agx.batch", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="batch_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="batch_id.currency_id", store=True, readonly=True
    )

    scrap_type = fields.Selection(
        [
            ("natural_loss",   "Natural Loss / Shrinkage"),
            ("quality_reject", "Quality Reject"),
            ("damage",         "Physical Damage"),
            ("disease",        "Disease / Pest"),
            ("other",          "Other"),
        ],
        required=True,
        default="natural_loss",
        string="Scrap Type",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        help="Leave empty to use the batch input product.",
    )
    scrap_qty = fields.Float(required=True, default=0.0, string="Qty Lost")
    uom_id = fields.Many2one("uom.uom", string="UoM")
    reason = fields.Char(
        string="Reason / Note",
        help="Required when scrap_type = 'other'.",
    )
    cost_impact = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_cost_impact",
        store=True,
        help="Estimated cost of this loss = scrap_qty × avg input cost per unit.",
    )

    @api.depends("scrap_qty", "batch_id.actual_raw_material_cost", "batch_id.input_qty")
    def _compute_cost_impact(self):
        for rec in self:
            if rec.batch_id.input_qty and rec.batch_id.actual_raw_material_cost:
                cost_per_unit = (
                    rec.batch_id.actual_raw_material_cost / rec.batch_id.input_qty
                )
                rec.cost_impact = rec.scrap_qty * cost_per_unit
            else:
                rec.cost_impact = 0.0

    @api.constrains("scrap_type", "reason")
    def _check_other_reason(self):
        for rec in self:
            if rec.scrap_type == "other" and not rec.reason:
                raise ValidationError(
                    _("Please provide a reason when scrap type is 'Other'.")
                )

    @api.constrains("scrap_qty")
    def _check_qty(self):
        for rec in self:
            if rec.scrap_qty < 0:
                raise ValidationError(_("Scrap quantity cannot be negative."))

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id


# ════════════════════════════════════════════════════════════════════
# Packaging Materials Cost
# ════════════════════════════════════════════════════════════════════
class AgxPackagingMaterial(models.Model):
    """Packaging materials reference table.

    Defines reusable packaging items (cartons, labels, pallets, etc.)
    with a standard unit cost that can be used across batches.
    """

    _name = "agx.packaging.material"
    _description = "Packaging Material"
    _order = "sequence, name"

    sequence  = fields.Integer(default=10)
    name      = fields.Char(required=True)
    code      = fields.Char()
    active    = fields.Boolean(default=True)
    material_type = fields.Selection(
        [
            ("carton",      "Carton / Box"),
            ("label",       "Label / Sticker"),
            ("pallet",      "Pallet"),
            ("wrapping",    "Wrapping / Film"),
            ("strap",       "Strap / Band"),
            ("other",       "Other"),
        ],
        required=True,
        default="carton",
    )
    product_id = fields.Many2one(
        "product.product",
        help="Optional Odoo product link for stock/accounting integration.",
    )
    standard_unit_cost = fields.Float(
        digits=(16, 4),
        help="Default cost per unit — can be overridden on each batch line.",
    )
    uom_id = fields.Many2one("uom.uom")
    note   = fields.Char()


class AgxBatchPackagingLine(models.Model):
    """One packaging material line on a Production Batch.

    Records the quantity and unit cost of each packaging item used
    in this batch so it is factored into the total allocable cost.
    """

    _name  = "agx.batch.packaging.line"
    _description = "Batch Packaging Material Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    company_id = fields.Many2one(
        related="batch_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="batch_id.currency_id", store=True, readonly=True
    )
    material_id = fields.Many2one(
        "agx.packaging.material",
        required=True,
        string="Material",
    )
    material_type = fields.Selection(
        related="material_id.material_type", store=True, readonly=True
    )
    qty     = fields.Float(required=True, default=1.0)
    uom_id  = fields.Many2one("uom.uom")
    unit_cost = fields.Float(
        digits=(16, 4),
        help="Cost per unit — defaults from material standard cost.",
    )
    total_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_total",
        store=True,
    )

    @api.depends("qty", "unit_cost")
    def _compute_total(self):
        for rec in self:
            rec.total_cost = rec.qty * rec.unit_cost

    @api.onchange("material_id")
    def _onchange_material(self):
        if self.material_id:
            self.unit_cost = self.material_id.standard_unit_cost
            self.uom_id    = self.material_id.uom_id

    @api.constrains("qty")
    def _check_qty(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError(_("Packaging quantity must be greater than zero."))
